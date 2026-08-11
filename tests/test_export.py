"""Tests for streaming exports."""

from __future__ import annotations

import csv as csv_module
import json
import sys

import pytest

from eudamed.export import FORMATS, _write_parquet, export_devices


class _PagingClient:
    def __init__(self, pages, details=None):
        self.pages = pages
        self.details = details or {}
        self.detail_calls = 0

    def iter_devices(self, page_size=300, max_pages=None, **filters):
        for page in self.pages:
            yield from page

    def basic_udi_detail(self, uuid):
        self.detail_calls += 1
        return self.details.get(uuid)


def test_jsonl_export_writes_one_object_per_line(tmp_path):
    client = _PagingClient([[{"uuid": "a", "basicUdi": "AAA"}],
                            [{"uuid": "b", "basicUdi": "BBB"}]])
    out = tmp_path / "devices.jsonl"
    report = export_devices(client, out, fmt="jsonl", cndCode="Z12")
    lines = out.read_text().strip().splitlines()
    assert report["records"] == 2
    assert [json.loads(line)["uuid"] for line in lines] == ["a", "b"]


def test_the_export_writes_a_manifest_recording_the_filters(tmp_path):
    """An extract whose query is not recorded cannot be replicated, and this API
    has no way to reconstruct a query after the fact."""
    client = _PagingClient([[{"uuid": "a", "basicUdi": "AAA"}]])
    out = tmp_path / "devices.jsonl"
    report = export_devices(client, out, fmt="jsonl", cndCode="Z12")
    manifest_path = tmp_path / "devices.jsonl.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["filters"] == {"cndCode": "Z12"}
    assert [f["path"] for f in manifest["files"]] == ["devices.jsonl"]
    assert report["manifest"] == str(manifest_path)


def test_two_exports_into_one_directory_keep_two_manifests(tmp_path):
    """I3. A single manifest.json per directory means the second export
    overwrites the first's provenance, leaving two data files and one manifest
    naming only one query -- with nothing on disk saying which file it
    describes."""
    client = _PagingClient([[{"uuid": "a"}]])
    export_devices(client, tmp_path / "mdr.jsonl", fmt="jsonl",
                   applicableLegislation="refdata.applicable-legislation.mdr")
    export_devices(client, tmp_path / "ivdr.jsonl", fmt="jsonl",
                   applicableLegislation="refdata.applicable-legislation.ivdr")

    mdr = json.loads((tmp_path / "mdr.jsonl.manifest.json").read_text())
    ivdr = json.loads((tmp_path / "ivdr.jsonl.manifest.json").read_text())
    assert mdr["filters"]["applicableLegislation"].endswith(".mdr")
    assert ivdr["filters"]["applicableLegislation"].endswith(".ivdr")
    assert [f["path"] for f in mdr["files"]] == ["mdr.jsonl"]
    assert [f["path"] for f in ivdr["files"]] == ["ivdr.jsonl"]


def test_the_manifest_names_only_the_files_this_export_wrote(tmp_path):
    """I4. Exporting into a home or project directory used to rglob it and
    SHA-256 every file found, recording each path: a performance trap on a
    large tree and a disclosure of filenames that have nothing to do with the
    extract."""
    (tmp_path / "tax-return-2025.pdf").write_bytes(b"not part of the export")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "notes.txt").write_text("private")

    client = _PagingClient([[{"uuid": "a"}]])
    out = tmp_path / "devices.jsonl"
    export_devices(client, out, fmt="jsonl")

    manifest = json.loads((tmp_path / "devices.jsonl.manifest.json").read_text())
    assert [f["path"] for f in manifest["files"]] == ["devices.jsonl"]
    assert manifest["n_files"] == 1
    assert "tax-return-2025.pdf" not in json.dumps(manifest)
    assert "notes.txt" not in json.dumps(manifest)


def test_csv_export_unions_keys_across_records(tmp_path):
    """Records are ragged — fields present on one are absent on another. Taking
    the header from the first record silently drops columns."""
    client = _PagingClient([[{"uuid": "a"}, {"uuid": "b", "tradeName": "Widget"}]])
    out = tmp_path / "devices.csv"
    export_devices(client, out, fmt="csv")
    header = out.read_text().splitlines()[0]
    assert "tradeName" in header


def test_enrichment_is_off_by_default(tmp_path):
    client = _PagingClient([[{"uuid": "a", "basicUdi": "AAA"}]])
    export_devices(client, tmp_path / "d.jsonl", fmt="jsonl")
    assert client.detail_calls == 0


def test_enrichment_merges_the_basic_udi_record(tmp_path):
    client = _PagingClient([[{"uuid": "a", "basicUdi": "AAA"}]],
                           details={"a": {"deviceName": "Widget",
                                          "deviceCriterion": "STANDARD"}})
    out = tmp_path / "d.jsonl"
    export_devices(client, out, fmt="jsonl", enrich=True)
    record = json.loads(out.read_text().strip())
    assert record["deviceName"] == "Widget"
    assert record["deviceCriterion"] == "STANDARD"
    assert client.detail_calls == 1


def test_an_unknown_format_raises(tmp_path):
    with pytest.raises(ValueError):
        export_devices(_PagingClient([]), tmp_path / "d.xml", fmt="xml")
    assert "parquet" in FORMATS


def test_csv_export_leaves_no_temporary_buffer_behind(tmp_path):
    client = _PagingClient([[{"uuid": "a"}, {"uuid": "b", "tradeName": "Widget"}]])
    out = tmp_path / "devices.csv"
    export_devices(client, out, fmt="csv")
    assert list(tmp_path.glob("*.buffer.jsonl")) == []
    assert list(tmp_path.glob("*.part")) == []
    manifest = json.loads((tmp_path / "devices.csv.manifest.json").read_text())
    assert [f["path"] for f in manifest["files"]] == ["devices.csv"]


class _FailingClient:
    """Yields one record, then blows up -- simulates a connection dropping
    partway through a crawl."""

    def iter_devices(self, page_size=300, max_pages=None, **filters):
        yield {"uuid": "a"}
        raise RuntimeError("connection reset")

    def basic_udi_detail(self, uuid):  # pragma: no cover - not reached
        raise AssertionError("should not be called")


def test_a_mid_export_failure_does_not_leave_a_half_written_output(tmp_path):
    out = tmp_path / "devices.jsonl"
    with pytest.raises(RuntimeError):
        export_devices(_FailingClient(), out, fmt="jsonl")
    assert not out.exists()


def test_a_mid_export_failure_does_not_leave_a_half_written_csv(tmp_path):
    out = tmp_path / "devices.csv"
    with pytest.raises(RuntimeError):
        export_devices(_FailingClient(), out, fmt="csv")
    assert not out.exists()


def test_a_second_pass_csv_failure_does_not_leave_a_complete_looking_file(
    tmp_path, monkeypatch
):
    """The buffering pass and the CSV-writing pass are two different pieces of
    code with two different failure points. A failure injected in the first
    (as above, via `_FailingClient`) never reaches the second, where the CSV
    file at `out` is actually produced -- so it cannot exercise the guard that
    keeps a failure there from leaving a partial file at the destination
    path. This fails a row partway through the second pass instead."""
    calls = {"n": 0}
    original_writerow = csv_module.DictWriter.writerow

    def _flaky_writerow(self, rowdict):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return original_writerow(self, rowdict)

    monkeypatch.setattr(csv_module.DictWriter, "writerow", _flaky_writerow)

    client = _PagingClient([[{"uuid": "a"}, {"uuid": "b"}, {"uuid": "c"}]])
    out = tmp_path / "devices.csv"
    with pytest.raises(RuntimeError):
        export_devices(client, out, fmt="csv")
    assert not out.exists()


def test_parquet_export_round_trips_ragged_records(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")

    client = _PagingClient([[{"uuid": "a"}, {"uuid": "b", "tradeName": "Widget"}]])
    out = tmp_path / "devices.parquet"
    report = export_devices(client, out, fmt="parquet")

    table = pq.read_table(out)
    assert report["records"] == 2
    assert set(table.column_names) >= {"uuid", "tradeName"}
    rows = table.to_pylist()
    assert rows[0]["uuid"] == "a"
    assert rows[0]["tradeName"] is None
    assert rows[1] == {"uuid": "b", "tradeName": "Widget"}


def test_parquet_export_streams_row_groups_instead_of_materialising_everything(tmp_path):
    """The whole point of writing Parquet with `pyarrow.parquet.ParquetWriter`
    rather than a single `pandas.DataFrame` is that a batch, not the full
    record set, is what ever sits in memory. Multiple row groups on disk is
    the observable trace that batching actually happened."""
    pq = pytest.importorskip("pyarrow.parquet")

    records = ({"uuid": str(i)} for i in range(5))
    out = tmp_path / "batched.parquet"
    n = _write_parquet(records, out, batch_size=2)
    assert n == 5
    assert pq.ParquetFile(out).metadata.num_row_groups == 3


def test_parquet_export_raises_a_clear_error_when_pyarrow_is_absent(tmp_path, monkeypatch):
    """Simulated rather than relying on an uninstalled package: setting a
    module to `None` in `sys.modules` makes the interpreter raise
    `ImportError` on import, exactly as it would for a genuinely missing
    dependency."""
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    client = _PagingClient([[{"uuid": "a"}]])
    with pytest.raises(ImportError, match="parquet"):
        export_devices(client, tmp_path / "d.parquet", fmt="parquet")
