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

Every export writes ``manifest.json`` beside the output via
``provenance.write_manifest``, with the filters recorded in the ``extra``
block. The EUDAMED public API offers no way to reconstruct a query after the
fact, so an export whose filters are not recorded on disk cannot be
replicated.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from eudamed.provenance import write_manifest

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


def _write_csv(records: Iterator[dict[str, Any]], out: Path) -> int:
    """Buffer to a temporary JSONL file, union the keys, then write the CSV.

    The buffer is removed once the CSV has been written in full. If the second
    pass raises, the buffer is left on disk under its own name rather than
    silently vanishing, and ``out`` is never left holding a partial file.
    """
    buffer = out.with_suffix(out.suffix + ".buffer.jsonl")
    n = _write_jsonl(records, buffer)

    fieldnames: list[str] = []
    seen: set[str] = set()
    with buffer.open(encoding="utf-8") as fh:
        for line in fh:
            for key in json.loads(line):
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)

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


def _write_parquet(records: Iterator[dict[str, Any]], out: Path) -> int:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "parquet export requires pandas and pyarrow; install the "
            "'parquet' extra: pip install eudamed-toolkit[parquet]"
        ) from exc

    buffer = out.with_suffix(out.suffix + ".buffer.jsonl")
    n = _write_jsonl(records, buffer)

    rows = []
    with buffer.open(encoding="utf-8") as fh:
        for line in fh:
            rows.append(json.loads(line))

    tmp_out = out.with_suffix(out.suffix + ".part")
    pd.DataFrame(rows).to_parquet(tmp_out)
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
    silently drop fields; Parquet does the same via pandas, imported lazily so
    the core package's only runtime dependency stays ``requests``.

    ``enrich=True`` follows every yielded record to its Basic UDI-DI detail to
    merge in ``deviceName``, ``deviceCriterion`` and the certificate list, none
    of which the search endpoint returns. That is **one request per device**:
    fine for a filtered pull of a few thousand, but the difference between a
    10,000-request export and a 3-million-request one is not visible in the
    boolean, so filter before enabling it on anything close to the full
    register.

    Returns ``{"records": int, "path": str, "manifest": str, "filters": dict}``.
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

    manifest_path = write_manifest(
        out.parent, label=out.stem, extra={"filters": filters, "format": fmt, "enrich": enrich}
    )

    return {
        "records": n,
        "path": str(out),
        "manifest": str(manifest_path),
        "filters": filters,
    }
