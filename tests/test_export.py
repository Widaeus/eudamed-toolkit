"""Tests for streaming exports."""

from __future__ import annotations

import csv as csv_module
import json
import sys

import pytest

from eudamed.errors import RequestFailed
from eudamed.export import FORMATS, _write_parquet, export_devices


class _PagingClient:
    def __init__(self, pages, details=None):
        self.pages = pages
        self.details = details or {}
        self.detail_calls = 0
        self.pages_served = []

    def search_devices(self, page=0, page_size=300, **filters):
        self.pages_served.append(page)
        content = self.pages[page] if page < len(self.pages) else []
        return {"content": content, "last": page >= len(self.pages) - 1}

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
    """Serves one page, then blows up -- simulates a connection dropping
    partway through a crawl."""

    def search_devices(self, page=0, page_size=300, **filters):
        if page == 0:
            return {"content": [{"uuid": "a"}], "last": False}
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


# --------------------------------------------------------------------- resume


class _ResumableClient:
    """Serves fixed pages and raises `RequestFailed` on the pages named in
    `fail_on`, so a crawl can be made to die at a chosen boundary and then be
    restarted against a differently-drifted register."""

    def __init__(self, pages, fail_on=()):
        self.pages = pages
        self.fail_on = set(fail_on)
        self.pages_served = []

    def search_devices(self, page=0, page_size=300, **filters):
        if page in self.fail_on:
            raise RequestFailed(
                "https://example.invalid/devices/udiDiData",
                {"page": page},
                status=503,
                attempts=8,
            )
        self.pages_served.append(page)
        content = self.pages[page] if page < len(self.pages) else []
        more_to_come = any(p > page for p in self.fail_on)
        return {"content": content,
                "last": page >= len(self.pages) - 1 and not more_to_come}

    def basic_udi_detail(self, uuid):  # pragma: no cover - not reached
        raise AssertionError("should not be called")


def _uuids(path):
    return [json.loads(line)["uuid"] for line in path.read_text().strip().splitlines()]


def test_a_failed_crawl_leaves_a_checkpoint_and_keeps_its_partial_output(tmp_path):
    """Since a page that cannot be fetched raises rather than truncating
    silently, a crawl that dies at the last page yields nothing at all unless
    the pages already fetched are kept along with a note of where to pick up."""
    client = _ResumableClient([[{"uuid": "a"}, {"uuid": "b"}]], fail_on=[1])
    out = tmp_path / "devices.jsonl"

    with pytest.raises(RequestFailed):
        export_devices(client, out, fmt="jsonl", cndCode="Z12")

    checkpoint = json.loads((tmp_path / "devices.jsonl.progress.json").read_text())
    assert checkpoint["next_page"] == 1
    assert checkpoint["records_written"] == 2
    assert checkpoint["last_page_uuids"] == ["a", "b"]
    assert checkpoint["filters"] == {"cndCode": "Z12"}

    assert not out.exists()
    partial = tmp_path / "devices.jsonl.part"
    assert _uuids(partial) == ["a", "b"]


def test_resuming_continues_from_the_recorded_page_and_unions_both_runs(tmp_path):
    out = tmp_path / "devices.jsonl"
    first = _ResumableClient([[{"uuid": "a"}, {"uuid": "b"}]], fail_on=[1])
    with pytest.raises(RequestFailed):
        export_devices(first, out, fmt="jsonl", cndCode="Z12")

    second = _ResumableClient([[{"uuid": "a"}, {"uuid": "b"}],
                               [{"uuid": "c"}, {"uuid": "d"}]])
    report = export_devices(second, out, fmt="jsonl", resume=True, cndCode="Z12")

    assert second.pages_served == [1]
    assert _uuids(out) == ["a", "b", "c", "d"]
    assert report["records"] == 4


def test_a_record_duplicated_across_the_seam_is_written_once(tmp_path):
    """The search endpoint has no server-side sort and the register changes
    daily, so a record can slide from one page to the next while the crawl is
    paused. The checkpoint's record of the last page written is what stops it
    being written twice."""
    out = tmp_path / "devices.jsonl"
    first = _ResumableClient([[{"uuid": "a"}, {"uuid": "b"}]], fail_on=[1])
    with pytest.raises(RequestFailed):
        export_devices(first, out, fmt="jsonl")

    # `b` has drifted back onto page 1 by the time the crawl is restarted.
    second = _ResumableClient([[{"uuid": "a"}], [{"uuid": "b"}, {"uuid": "c"}]])
    report = export_devices(second, out, fmt="jsonl", resume=True)

    assert _uuids(out) == ["a", "b", "c"]
    assert report["records"] == 3


def test_resuming_with_different_filters_refuses_and_names_the_field(tmp_path):
    """Resuming one query into another query's output file would blend two
    result sets into one file carrying one manifest."""
    out = tmp_path / "devices.jsonl"
    with pytest.raises(RequestFailed):
        export_devices(
            _ResumableClient([[{"uuid": "a"}]], fail_on=[1]), out,
            fmt="jsonl", cndCode="Z12",
        )

    with pytest.raises(ValueError, match="filters"):
        export_devices(
            _ResumableClient([[{"uuid": "a"}]]), out,
            fmt="jsonl", resume=True, cndCode="W99",
        )


def test_resuming_with_a_different_format_refuses_and_names_the_field(tmp_path):
    out = tmp_path / "devices.jsonl"
    with pytest.raises(RequestFailed):
        export_devices(
            _ResumableClient([[{"uuid": "a"}]], fail_on=[1]), out, fmt="jsonl",
        )

    with pytest.raises(ValueError, match="fmt"):
        export_devices(
            _ResumableClient([[{"uuid": "a"}]]), out, fmt="csv", resume=True,
        )


def test_a_completed_export_removes_its_checkpoint(tmp_path):
    client = _PagingClient([[{"uuid": "a"}], [{"uuid": "b"}]])
    out = tmp_path / "devices.jsonl"
    export_devices(client, out, fmt="jsonl")
    assert list(tmp_path.glob("*.progress.json")) == []


def test_a_resumed_export_is_marked_as_resumed_in_its_manifest(tmp_path):
    """A stitched-together extract must be distinguishable on disk from a
    single-pass one: it is not a point-in-time snapshot."""
    out = tmp_path / "devices.jsonl"
    with pytest.raises(RequestFailed):
        export_devices(_ResumableClient([[{"uuid": "a"}]], fail_on=[1]), out)

    export_devices(
        _ResumableClient([[{"uuid": "a"}], [{"uuid": "b"}]]), out, resume=True
    )

    manifest = json.loads((tmp_path / "devices.jsonl.manifest.json").read_text())
    assert manifest["resumed"] is True
    assert manifest["resume_points"] == 1


def test_a_clean_export_is_marked_as_not_resumed_in_its_manifest(tmp_path):
    client = _PagingClient([[{"uuid": "a"}]])
    out = tmp_path / "devices.jsonl"
    export_devices(client, out, fmt="jsonl")
    manifest = json.loads((tmp_path / "devices.jsonl.manifest.json").read_text())
    assert manifest["resumed"] is False
    assert manifest["resume_points"] == 0


def test_without_resume_an_existing_checkpoint_is_started_over_not_continued(tmp_path):
    """Resuming is opt-in. A re-run that does not ask for it must produce a
    single-pass extract, not silently inherit a previous run's records."""
    out = tmp_path / "devices.jsonl"
    with pytest.raises(RequestFailed):
        export_devices(_ResumableClient([[{"uuid": "a"}, {"uuid": "b"}]], fail_on=[1]), out)

    second = _ResumableClient([[{"uuid": "a"}, {"uuid": "b"}],
                               [{"uuid": "c"}, {"uuid": "d"}]])
    export_devices(second, out, fmt="jsonl")

    assert second.pages_served == [0, 1]
    assert _uuids(out) == ["a", "b", "c", "d"]
    manifest = json.loads((tmp_path / "devices.jsonl.manifest.json").read_text())
    assert manifest["resumed"] is False
