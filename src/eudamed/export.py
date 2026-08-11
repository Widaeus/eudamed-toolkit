"""Streaming exports of filtered device searches to JSONL, CSV or Parquet.

The unfiltered register is 2.98 million UDI-DI records; nothing here holds a
result set in memory. JSONL is written straight through, one line per record,
as pages arrive from ``client.iter_devices``. CSV cannot be streamed directly
because the records are ragged -- a field present on one record is absent from
another -- so taking the header from the first record would silently drop
every column that first record happens not to have. CSV export therefore
buffers to a temporary JSONL file, unions the keys of every record in a second
pass to build the header, then writes the CSV from that buffer. The temporary
file is removed once the CSV is written successfully, and left in place (named
for inspection) if the second pass raises, since a half-written CSV is worse
than a leftover buffer.

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
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from eudamed.provenance import write_file_manifest

FORMATS = ("jsonl", "csv", "parquet")


class _DeviceSource(Protocol):
    def iter_devices(
        self, page_size: int = ..., max_pages: int | None = ..., **filters: Any
    ) -> Iterator[dict[str, Any]]: ...

    def basic_udi_detail(self, uuid: str) -> dict[str, Any] | None: ...


def _records(
    client: _DeviceSource,
    enrich: bool,
    progress: Callable[[int], None] | None,
    filters: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Yield one dict per device, optionally enriched from its Basic UDI-DI detail.

    ``enrich=True`` issues one additional request per device -- fine for a
    filtered pull of a few thousand records, ruinous for an unfiltered one.
    """
    n = 0
    for record in client.iter_devices(**filters):
        if enrich:
            uuid = record.get("uuid")
            detail = client.basic_udi_detail(uuid) if uuid else None
            if detail:
                record = {**record, **detail}
        yield record
        n += 1
        if progress is not None:
            progress(n)


def _write_jsonl(records: Iterator[dict[str, Any]], out: Path) -> int:
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    return n


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


def _write_csv(records: Iterator[dict[str, Any]], out: Path) -> int:
    """Buffer to a temporary JSONL file, union the keys, then write the CSV.

    The buffer is removed once the CSV has been written in full. If either
    pass raises, ``out`` is never left holding a partial file: the CSV itself
    is built under a ``.part`` name and only renamed onto ``out`` once
    ``csv.DictWriter`` has finished without error.
    """
    buffer = out.with_suffix(out.suffix + ".buffer.jsonl")
    n = _write_jsonl(records, buffer)

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
    buffer.unlink()
    return n


# Rows held in memory at once while writing a Parquet row group. An unfiltered
# export is 2.98 million records; this keeps memory bounded to one batch
# regardless of how large the export is.
_PARQUET_BATCH_SIZE = 50_000


def _write_parquet(
    records: Iterator[dict[str, Any]], out: Path, batch_size: int = _PARQUET_BATCH_SIZE
) -> int:
    """Buffer to a temporary JSONL file, then stream it into Parquet row groups.

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
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError(
            "parquet export requires pyarrow; install the 'parquet' extra: "
            "pip install eudamed-toolkit[parquet]"
        ) from exc

    buffer = out.with_suffix(out.suffix + ".buffer.jsonl")
    n = _write_jsonl(records, buffer)

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
    buffer.unlink()
    return n


def export_devices(
    client: _DeviceSource,
    out: Path,
    fmt: str = "jsonl",
    enrich: bool = False,
    progress: Callable[[int], None] | None = None,
    **filters: Any,
) -> dict[str, Any]:
    """Stream a filtered device search to disk and record it in a manifest.

    Pages are written as they arrive rather than accumulated -- an unfiltered
    export is 2.98 million records and will not fit in memory. ``fmt`` selects
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

    Returns ``{"records": int, "path": str, "manifest": str, "filters": dict}``,
    where ``manifest`` is ``<out>.manifest.json``.
    Raises ``ValueError`` if ``fmt`` is not one of ``FORMATS``.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; choose one of {FORMATS}")

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    records = _records(client, enrich, progress, filters)

    if fmt == "jsonl":
        tmp_out = out.with_suffix(out.suffix + ".part")
        n = _write_jsonl(records, tmp_out)
        tmp_out.replace(out)
    elif fmt == "csv":
        n = _write_csv(records, out)
    else:
        n = _write_parquet(records, out)

    manifest_path = write_file_manifest(
        [out],
        out.with_name(out.name + ".manifest.json"),
        label=out.stem,
        extra={"filters": filters, "format": fmt, "enrich": enrich},
    )

    return {
        "records": n,
        "path": str(out),
        "manifest": str(manifest_path),
        "filters": filters,
    }
