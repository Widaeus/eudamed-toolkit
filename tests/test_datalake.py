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


def test_a_rejected_filter_raises_before_any_request(tmp_path):
    """DEVICE_CRITERION, DEVICE_STATUS_TYPE_ID, LATEST_VERSION and the boolean
    flag columns are columns of the export but not accepted as query
    parameters: the service answers HTTP 400 with an empty body. Refusing
    them locally names the problem instead of surfacing
    it as a failed request."""
    with pytest.raises(ValueError, match="DEVICE_CRITERION"):
        DataLakeClient(run_log=tmp_path / "dl.jsonl").fetch(DEVICE_CRITERION="LEGACY")


def test_risk_class_and_legislation_are_accepted_filters(tmp_path, monkeypatch):
    """An earlier version listed RISK_CLASS_ID as inert. It filters -- the
    reference IDs are negative integers (Class IIa is -204), and a guessed
    positive one matches nothing, which is presumably how it was misread."""
    sent = []
    monkeypatch.setattr(
        DataLakeClient, "_get_csv", lambda self, params: sent.append(params) or CSV
    )
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl")
    client.fetch(RISK_CLASS_ID=-204, APPLICABLE_LEGISLATION_ID=-197)
    assert sent[0]["RISK_CLASS_ID"] == -204
    assert sent[0]["APPLICABLE_LEGISLATION_ID"] == -197


def test_nomenclature_code_is_sent_with_the_leading_space_the_export_stores(
    tmp_path, monkeypatch
):
    """Every NOMENCLATURE_CODE value in the export carries a leading space
    (' Z12110102'), and the filter is an exact match, so the code as a person
    writes it returns zero rows -- silently, with HTTP 200. The client sends
    the stored form."""
    sent = []
    monkeypatch.setattr(
        DataLakeClient, "_get_csv", lambda self, params: sent.append(params) or CSV
    )
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl")
    client.fetch(NOMENCLATURE_CODE="Z12110102")
    assert sent[0]["NOMENCLATURE_CODE"] == " Z12110102"
    client.fetch(NOMENCLATURE_CODE=" Z12110102")
    assert sent[1]["NOMENCLATURE_CODE"] == " Z12110102"


def test_the_body_is_decoded_as_utf8_despite_the_missing_charset_header(
    tmp_path, fake_session
):
    """The endpoint serves UTF-8 as bare ``text/csv``. Without a charset,
    ``requests`` decodes ``.text`` as ISO-8859-1 and every accented
    manufacturer name comes back mangled ('FKG Dentaire SÃ rl')."""
    body = "MF_SRN,MF_NAME\nCH-MF-000000001,FKG Dentaire Sàrl\n"
    fake_session.queue(FakeResponse(
        200, content=body.encode("utf-8"),
        text=body.encode("utf-8").decode("iso-8859-1"),
        headers={"Content-Type": "text/csv"},
    ))
    client = DataLakeClient(run_log=tmp_path / "dl.jsonl", min_interval=0.0)
    result = client.fetch(MF_SRN="CH-MF-000000001")
    assert result.rows[0]["MF_NAME"] == "FKG Dentaire Sàrl"


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
