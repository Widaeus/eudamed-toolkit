"""Streaming exports of filtered device searches to JSONL, CSV or Parquet.

The unfiltered register is over three million UDI-DI records; nothing here holds a
result set in memory. Pages are walked one at a time and written as they
arrive. JSONL is written straight through, one line per record. CSV cannot be
streamed directly because the records are ragged -- a field present on one
record is absent from another -- so taking the header from the first record
would silently drop every column that first record happens not to have. CSV
export therefore buffers to a temporary JSONL file, unions the keys of every
record in a second pass to build the header, then writes the CSV from that
buffer. The temporary file is removed once the CSV is written successfully,
and left in place (named for inspection) if the second pass raises, since a
half-written CSV is worse than a leftover buffer.

Every export writes ``<out>.manifest.json`` beside the output via
``provenance.write_file_manifest``, with the filters recorded in the ``extra``
block. The EUDAMED public API offers no way to reconstruct a query after the
fact, so an export whose filters are not recorded on disk cannot be
replicated. The manifest is named after the output file rather than being a
single ``manifest.json`` per directory, because two exports into one
directory would otherwise leave one manifest describing one of them and two
data files -- and nothing on disk saying which. It hashes only the files this
export wrote: pointed at a whole directory it would hash and name every
unrelated file sitting next to the output, which is slow on a large tree and
discloses filenames that are nobody's business.

Each completed page also updates ``<out>.progress.json``, so a crawl that
dies at page 140 of 149 can be restarted from page 140 with ``resume=True``
rather than from the beginning. This is a saving of requests, not a
guarantee of coherence: see ``export_devices`` for what a resumed extract is
and is not.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from eudamed.provenance import utc_now, write_file_manifest

FORMATS = ("jsonl", "csv", "parquet")

log = logging.getLogger("eudamed.export")

# Checkpoint fields that describe *which* export the partial file belongs to,
# as opposed to how far it got. All four must match the current call before a
# resume is allowed.
_IDENTITY_KEYS = ("filters", "fmt", "page_size", "enrich")


class _DeviceSource(Protocol):
    def search_devices(
        self, page: int = ..., page_size: int = ..., **filters: Any
    ) -> dict[str, Any]: ...

    def basic_udi_detail(self, uuid: str) -> dict[str, Any] | None: ...


def _iter_pages(
    client: _DeviceSource,
    page_size: int,
    filters: dict[str, Any],
    start_page: int = 0,
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """Yield ``(page_number, records)`` for each page of a filtered search.

    ``client.iter_devices`` flattens the pages away, and page boundaries are
    exactly what a checkpoint has to record: the only thing the search
    endpoint lets a restarted crawl skip is whole pages. This walks
    ``client.search_devices`` directly instead, which returns the page
    envelope including ``last``.

    A page that cannot be fetched raises ``RequestFailed`` from the client and
    that exception is left to propagate, as everywhere else in this package: a
    crawl that stopped early must not be indistinguishable from one that
    finished.
    """
    page_no = start_page
    while True:
        page = client.search_devices(page=page_no, page_size=page_size, **filters)
        content = page.get("content") or []
        if not content:
            return
        yield page_no, content
        if page.get("last"):
            return
        page_no += 1


# ------------------------------------------------------------------ checkpoint


def checkpoint_path(out: Path) -> Path:
    """Return the progress file that belongs to output file ``out``."""
    return Path(out).with_name(Path(out).name + ".progress.json")


def _write_checkpoint(path: Path, state: dict[str, Any]) -> None:
    """Replace the checkpoint atomically.

    Written after every completed page, which is also the moment a crawl is
    most likely to be killed; a half-written progress file would be read back
    as a corrupt one and cost the whole crawl.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_checkpoint(
    path: Path, identity: dict[str, Any], stream_path: Path
) -> dict[str, Any] | None:
    """Read the checkpoint for ``identity``, or ``None`` if there is none to use.

    Raises ``ValueError`` naming the field that differs when the checkpoint
    describes a different export. Resuming a ``cndCode=Z12`` crawl into a
    ``cndCode=W99`` output file would blend two result sets into one file
    carrying one manifest, and nothing on disk would record that it had
    happened.

    A checkpoint whose partial file has gone missing or been truncated behind
    its back is discarded rather than trusted, and the crawl starts over: the
    cost is requests, whereas trusting it would append onto an unknown prefix.
    """
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("checkpoint %s is unreadable; starting the export over", path)
        return None

    for key in _IDENTITY_KEYS:
        if state.get(key) != identity[key]:
            raise ValueError(
                f"checkpoint {path} belongs to a different export: {key} is "
                f"{state.get(key)!r} there and {identity[key]!r} here. Resuming "
                "would blend two result sets into one file and one manifest; "
                "re-run without resume, or export to a different path."
            )

    written = int(state.get("bytes_written", 0))
    if not stream_path.exists() or stream_path.stat().st_size < written:
        log.warning(
            "partial output %s is missing or shorter than the checkpoint records; "
            "starting the export over",
            stream_path,
        )
        return None
    return state


def _stream_pages_to_jsonl(
    client: _DeviceSource,
    stream_path: Path,
    progress_path: Path,
    identity: dict[str, Any],
    progress: Callable[[int], None] | None,
    resume: bool,
) -> tuple[int, int]:
    """Write every matching record to ``stream_path`` as JSONL, checkpointing.

    Returns ``(records_written, resume_points)``.

    After each completed page the file is flushed and ``progress_path`` is
    updated with the next page to fetch, the byte length written so far, and
    the uuids the page just handled was served. An interrupted crawl leaves
    both files behind rather than cleaning up after itself, and a later call
    with ``resume=True`` truncates the partial file back to the last
    page-aligned byte offset -- discarding any records from the page that was
    in flight when the crawl died -- and carries on from the next page.

    **The seam guard is partial and cannot be made complete.** The search
    endpoint offers no server-side sort, and the register changes daily, so
    page boundaries do not hold between runs: one device registered or
    withdrawn during the pause shifts every later record by one position.
    Skipping the uuids the last page was served catches drift smaller than a
    page, which is the common case. Drift of a page or more will duplicate
    records that moved forwards and lose records that moved backwards, and
    nothing available from this API can detect that it happened -- there is no
    "changed since" filter, and ``lastUpdateDate`` is null in search
    responses. That is why the manifest records the resume rather than the
    guard being presented as a fix.
    """
    state = _load_checkpoint(progress_path, identity, stream_path) if resume else None

    if state is None:
        # A fresh start invalidates any checkpoint left by an earlier attempt;
        # remove it now rather than leaving a stale one describing a file this
        # run is about to overwrite.
        progress_path.unlink(missing_ok=True)
        start_page, n, written, resume_points = 0, 0, 0, 0
        skip: set[str] = set()
        mode = "wb"
    else:
        start_page = int(state["next_page"])
        n = int(state["records_written"])
        written = int(state["bytes_written"])
        resume_points = int(state.get("resume_points", 0)) + 1
        skip = set(state.get("last_page_uuids") or [])
        with stream_path.open("r+b") as fh:
            fh.truncate(written)
        mode = "ab"
        log.info("resuming %s from page %d (%d records already written)",
                 stream_path, start_page, n)

    with stream_path.open(mode) as fh:
        for page_no, content in _iter_pages(
            client, identity["page_size"], identity["filters"], start_page
        ):
            # The seam is a property of the page as *served*, not of the
            # records this run happened to write. A resumed page whose every
            # record was skipped writes nothing, and recording the written
            # records would store an empty seam -- leaving the next resume
            # with no guard at all, and writing those records twice.
            page_uuids = [r["uuid"] for r in content if r.get("uuid") is not None]
            for record in content:
                uuid = record.get("uuid")
                if uuid is not None and uuid in skip:
                    continue
                if identity["enrich"] and uuid:
                    detail = client.basic_udi_detail(uuid)
                    if detail:
                        record = {**record, **detail}
                line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
                fh.write(line)
                written += len(line)
                n += 1
                if progress is not None:
                    progress(n)
            # The guard applies to the first page fetched after a resume only:
            # that is the only boundary stitched from two moments in time.
            skip = set()
            fh.flush()
            _write_checkpoint(progress_path, {
                **identity,
                "next_page": page_no + 1,
                "records_written": n,
                "bytes_written": written,
                "last_page_uuids": page_uuids,
                "resume_points": resume_points,
                "updated_utc": utc_now(),
            })

    return n, resume_points


# --------------------------------------------------------------------- writers


def _scan_fieldnames(buffer: Path) -> list[str]:
    """Return the union of keys across every record in a JSONL buffer.

    Order is first-seen. Shared by the CSV and Parquet writers: both need a
    header (respectively a schema) fixed once, up front, over the *whole*
    file, because the records are ragged -- a field present on one record is
    absent from another -- and taking it from the first record, or letting it
    drift between batches, would silently drop or rearrange columns.
    """
    fieldnames: list[str] = []
    seen: set[str] = set()
    with buffer.open(encoding="utf-8") as fh:
        for line in fh:
            for key in json.loads(line):
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    return fieldnames


def _csv_from_buffer(buffer: Path, out: Path) -> None:
    """Union the keys of a JSONL buffer, then write the CSV in a second pass.

    ``out`` is never left holding a partial file: the CSV is built under a
    ``.part`` name and only renamed onto ``out`` once ``csv.DictWriter`` has
    finished without error.
    """
    fieldnames = _scan_fieldnames(buffer)

    tmp_out = out.with_suffix(out.suffix + ".part")
    with (
        buffer.open(encoding="utf-8") as fh,
        tmp_out.open("w", newline="", encoding="utf-8") as out_fh,
    ):
        writer = csv.DictWriter(out_fh, fieldnames=fieldnames)
        writer.writeheader()
        for line in fh:
            writer.writerow(json.loads(line))
    tmp_out.replace(out)


# Rows held in memory at once while writing a Parquet row group. An unfiltered
# export is over three million records; this keeps memory bounded to one batch
# regardless of how large the export is.
_PARQUET_BATCH_SIZE = 50_000


def _require_pyarrow() -> tuple[Any, Any]:
    """Import ``pyarrow`` or raise with the extra to install.

    Checked before the crawl starts, not after: discovering the dependency is
    missing at the end of a several-hour export would throw the whole thing
    away.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError(
            "parquet export requires pyarrow; install the 'parquet' extra: "
            "pip install eudamed-toolkit[parquet]"
        ) from exc
    return pa, pq


def _parquet_from_buffer(
    buffer: Path, out: Path, batch_size: int = _PARQUET_BATCH_SIZE
) -> None:
    """Stream a JSONL buffer into Parquet row groups.

    The schema is fixed once, from the same whole-file key-union pass the CSV
    writer uses (`_scan_fieldnames`), so every row group shares an identical
    schema even though the records are ragged. From there the buffer is read
    and written in batches of `batch_size` rows: only one batch is ever held
    in memory, never the full record set materialised as a single table or
    data frame, which is the case an unfiltered, multi-million-record export
    would need this format to survive.

    Every field is written as its string form (``None`` stays null; lists and
    dicts -- such as the certificate list `enrich=True` merges in -- are
    JSON-encoded). That keeps every row group's schema identical regardless of
    which type a given field happens to take on a given record; without it, a
    field that is an integer on one record and a list on the next would force
    a schema change partway through the file.
    """
    pa, pq = _require_pyarrow()

    fieldnames = _scan_fieldnames(buffer)
    schema = pa.schema([(name, pa.string()) for name in fieldnames])

    def _cell(value: Any) -> str | None:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    tmp_out = out.with_suffix(out.suffix + ".part")
    writer = pq.ParquetWriter(tmp_out, schema)
    try:
        with buffer.open(encoding="utf-8") as fh:
            batch: list[dict[str, str | None]] = []
            for line in fh:
                record = json.loads(line)
                batch.append({name: _cell(record.get(name)) for name in fieldnames})
                if len(batch) >= batch_size:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch = []
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    finally:
        writer.close()
    tmp_out.replace(out)


# ---------------------------------------------------------------------- export


def export_devices(
    client: _DeviceSource,
    out: Path,
    fmt: str = "jsonl",
    enrich: bool = False,
    progress: Callable[[int], None] | None = None,
    resume: bool = False,
    page_size: int = 300,
    **filters: Any,
) -> dict[str, Any]:
    """Stream a filtered device search to disk and record it in a manifest.

    Pages are written as they arrive rather than accumulated -- an unfiltered
    export is over three million records and will not fit in memory. ``fmt`` selects
    one of ``FORMATS``: JSONL streams straight through; CSV buffers to a
    temporary JSONL file and unions record keys in a second pass, because
    device records are ragged and a header taken from the first record would
    silently drop fields; Parquet does the same to fix its schema, then
    streams the buffer into row groups one batch at a time via ``pyarrow``,
    imported lazily so the core package's only runtime dependency stays
    ``requests``.

    ``enrich=True`` follows every yielded record to its Basic UDI-DI detail to
    merge in ``deviceName``, ``deviceCriterion`` and the certificate list, none
    of which the search endpoint returns. That is **one request per device**:
    fine for a filtered pull of a few thousand, but the difference between a
    10,000-request export and a 3-million-request one is not visible in the
    boolean, so filter before enabling it on anything close to the full
    register.

    Every completed page updates ``<out>.progress.json``. A crawl that raises
    partway through leaves that file and its partial output in place, and
    ``resume=True`` on a later call picks up from the next unfetched page
    instead of starting over. **A resumed export is not a point-in-time
    snapshot.** It is stitched together from two or more moments, and because
    the search endpoint has no server-side sort while the register changes
    daily, page boundaries move between runs: records can be duplicated across
    the seam or missed entirely. The uuids of the last page written are
    skipped on resume, which catches drift smaller than one page; larger drift
    is undetectable through this API. The manifest therefore records
    ``resumed`` and ``resume_points`` so a stitched extract can be told from a
    clean one on disk.

    ``resume=False`` (the default) ignores and overwrites any existing
    checkpoint. ``resume=True`` raises ``ValueError`` naming the field that
    differs if the checkpoint's ``filters``, ``fmt``, ``page_size`` or
    ``enrich`` do not match this call.

    Returns ``{"records", "path", "manifest", "filters", "resumed",
    "resume_points"}``, where ``manifest`` is ``<out>.manifest.json``.
    Raises ``ValueError`` if ``fmt`` is not one of ``FORMATS``.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; choose one of {FORMATS}")

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "parquet":
        _require_pyarrow()

    identity = {"filters": filters, "fmt": fmt, "page_size": page_size, "enrich": enrich}
    suffix = ".part" if fmt == "jsonl" else ".buffer.jsonl"
    stream_path = out.with_suffix(out.suffix + suffix)
    progress_path = checkpoint_path(out)

    n, resume_points = _stream_pages_to_jsonl(
        client, stream_path, progress_path, identity, progress, resume
    )

    if fmt == "jsonl":
        stream_path.replace(out)
    else:
        if fmt == "csv":
            _csv_from_buffer(stream_path, out)
        else:
            _parquet_from_buffer(stream_path, out)
        stream_path.unlink()

    # Only once the output is complete: a second pass that raises leaves the
    # buffer and the checkpoint behind so the crawl need not be repeated.
    progress_path.unlink(missing_ok=True)

    manifest_path = write_file_manifest(
        [out],
        out.with_name(out.name + ".manifest.json"),
        label=out.stem,
        extra={
            "filters": filters,
            "format": fmt,
            "enrich": enrich,
            "resumed": resume_points > 0,
            "resume_points": resume_points,
        },
    )

    return {
        "records": n,
        "path": str(out),
        "manifest": str(manifest_path),
        "filters": filters,
        "resumed": resume_points > 0,
        "resume_points": resume_points,
    }
