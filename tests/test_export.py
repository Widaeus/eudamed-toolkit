"""Tests for streaming exports."""

from __future__ import annotations

import json

import pytest

from eudamed.export import FORMATS, export_devices


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
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["filters"] == {"cndCode": "Z12"}
    assert manifest["n_files"] >= 1
    assert report["manifest"].endswith("manifest.json")


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
