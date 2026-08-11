"""Tests for the Data Lake bulk client."""

from __future__ import annotations

import json

import pytest
import requests

from eudamed.datalake import ROW_CAP, DataLakeClient
from eudamed.errors import RequestFailed

from .conftest import FakeResponse

CSV = "BASIC_UDI,DEVICE_NAME\nAAA,Widget\nBBB,Gadget\n"


def test_a_full_page_is_flagged_as_truncated(tmp_path, monkeypatch):
    """The endpoint caps at 1,000 rows and offers no pagination — $top, limit,
    offset and page all 400. A query returning exactly 1,000 rows is presumed
    truncated, because a short answer that looks complete is the worst failure
    this client can produce."""
    rows = "BASIC_UDI\n" + "".join(f"X{i}\n" for i in range(ROW_CAP))
    monkeypatch.setattr(DataLakeClient, "_get_csv", lambda self, params: rows)
    result = DataLakeClient(run_log=tmp_path / "dl.jsonl").fetch(MF_SRN="DE-MF-000000001")
    assert len(result) == ROW_CAP
    assert result.truncated is True


def test_a_short_page_is_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(DataLakeClient, "_get_csv", lambda self, params: CSV)
    result = DataLakeClient(run_log=tmp_path / "dl.jsonl").fetch(MF_SRN="DE-MF-000000001")
    assert len(result) == 2
    assert result.truncated is False


def test_an_inert_filter_raises(tmp_path):
    """RISK_CLASS_ID, DEVICE_CRITERION, NOMENCLATURE_CODE and LATEST_VERSION are
    accepted by the endpoint and return nothing. Silently filtering to zero rows
    is indistinguishable from a device that does not exist."""
    with pytest.raises(ValueError):
        DataLakeClient(run_log=tmp_path / "dl.jsonl").fetch(RISK_CLASS_ID=-204)


def test_an_unknown_filter_raises(tmp_path):
    with pytest.raises(ValueError):
        DataLakeClient(run_log=tmp_path / "dl.jsonl").fetch(MANUFACTURER="Siemens")


def test_a_non_retryable_http_error_raises_rather_than_reading_as_no_rows(
    tmp_path, fake_session
):
    """I1. An HTTP 403 returned as an empty body makes an outage
    indistinguishable from a manufacturer with no registrations -- and the
    harvest then counts that manufacturer as successfully pulled."""
    fake_session.queue(FakeResponse(403))
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl", min_interval=0.0)
    with pytest.raises(RequestFailed) as excinfo:
        client.fetch(MF_SRN="DE-MF-000000001")
    assert excinfo.value.status == 403
    assert excinfo.value.params["MF_SRN"] == "DE-MF-000000001"


def test_exhausted_retries_raise_rather_than_returning_an_empty_result(
    tmp_path, fake_session
):
    fake_session.queue(FakeResponse(503), FakeResponse(503))
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl", min_interval=0.0, max_retries=2)
    with pytest.raises(RequestFailed) as excinfo:
        client.fetch(MF_SRN="DE-MF-000000001")
    assert excinfo.value.status == 503
    assert excinfo.value.attempts == 2


def test_a_transport_failure_raises_with_its_reason(tmp_path, monkeypatch):
    def boom(self, *args, **kwargs):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(requests.Session, "get", boom)
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl", min_interval=0.0, max_retries=2)
    with pytest.raises(RequestFailed) as excinfo:
        client.fetch(MF_SRN="DE-MF-000000001")
    assert excinfo.value.status is None
    assert "connection reset" in str(excinfo.value)


def test_an_empty_body_is_still_an_empty_result(tmp_path, fake_session):
    """The other half of I1: this endpoint answers 'no matching rows' with an
    empty body, and a manufacturer with no registrations is a real answer."""
    fake_session.queue(FakeResponse(200, text=""))
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl", min_interval=0.0)
    result = client.fetch(MF_SRN="DE-MF-000000001")
    assert len(result) == 0
    assert result.truncated is False


def test_harvest_counts_a_failed_manufacturer_as_failed_not_as_pulled(
    tmp_path, monkeypatch
):
    """A summary saying every manufacturer was pulled, when one of them was an
    outage, is how a gap becomes invisible."""
    from eudamed.datalake import Result

    def fake_by_manufacturer(self, srn):
        if srn == "DE-MF-000000002":
            raise RequestFailed("https://example.invalid", {"MF_SRN": srn}, status=503)
        return Result([{"BASIC_UDI": "AAA"}], False, {"MF_SRN": srn})

    monkeypatch.setattr(DataLakeClient, "by_manufacturer", fake_by_manufacturer)
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl", min_interval=0.0)
    out = tmp_path / "harvest.jsonl"

    summary = client.harvest(["DE-MF-000000001", "DE-MF-000000002"], out, workers=1)

    assert summary["manufacturers_pulled"] == 1
    assert summary["manufacturers_failed"] == 1
    assert summary["failed_srns"] == ["DE-MF-000000002"]
    assert summary["manufacturers_requested"] == 2
    written = [json.loads(line) for line in out.read_text().strip().splitlines()]
    assert [row["_query_srn"] for row in written] == ["DE-MF-000000001"]
