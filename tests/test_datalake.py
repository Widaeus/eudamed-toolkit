"""Tests for the Data Lake bulk client."""

from __future__ import annotations

import pytest

from eudamed.datalake import ROW_CAP, DataLakeClient

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
