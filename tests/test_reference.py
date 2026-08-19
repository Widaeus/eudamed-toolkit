# tests/test_reference.py
"""Tests for reference-value decoding."""

from __future__ import annotations

from eudamed.reference import ReferenceMaps, build_session

from .conftest import FakeResponse

CSV = (
    "ID,CODE,LANGUAGE,VALUE\n"
    "-204,RISK_CLASS_ID,en,Class IIa\n"
    "-204,RISK_CLASS_ID,fr,Classe IIa\n"
    "-205,RISK_CLASS_ID,en,Class IIb\n"
)


def test_only_english_labels_are_kept(monkeypatch):
    """The endpoint returns every language in one response. Keeping them all
    makes the last language written win, which is not a decision anyone made."""
    monkeypatch.setattr("eudamed.reference._get_csv", lambda code, session=None: CSV)
    maps = ReferenceMaps.load(cache=None)
    assert maps.risk_class("-204") == "Class IIa"
    assert maps.risk_class("-205") == "Class IIb"


def test_reference_requests_identify_the_tool_and_a_contact(monkeypatch):
    """These three requests used to go out through `requests.get` directly,
    carrying the library's default User-Agent on shared public
    infrastructure."""
    sessions = []

    def fake(code, session=None):
        sessions.append(session)
        return CSV

    monkeypatch.setattr("eudamed.reference._get_csv", fake)
    ReferenceMaps.load(cache=None, contact="someone@example.org")

    agents = {s.headers["User-Agent"] for s in sessions}
    assert len(agents) == 1
    agent = agents.pop()
    assert agent.startswith("eudamed-toolkit/")
    assert "someone@example.org" in agent


def test_the_default_session_still_identifies_the_tool():
    assert build_session().headers["User-Agent"].startswith("eudamed-toolkit/")


def test_an_unknown_id_returns_the_id_unchanged(monkeypatch):
    """Better a visible raw ID than a silent blank: a blank reads as 'this device
    has no risk class', which is never true."""
    monkeypatch.setattr("eudamed.reference._get_csv", lambda code, session=None: CSV)
    maps = ReferenceMaps.load(cache=None)
    assert maps.risk_class("-999") == "-999"


def test_the_maps_are_cached_to_disk(tmp_path, monkeypatch):
    calls = []

    def fake(code, session=None):
        calls.append(code)
        return CSV

    monkeypatch.setattr("eudamed.reference._get_csv", fake)
    cache = tmp_path / "reference_values.json"
    ReferenceMaps.load(cache=cache)
    first = len(calls)
    ReferenceMaps.load(cache=cache)
    assert len(calls) == first, "a rebuild must work offline"


def test_a_failed_fetch_yields_an_empty_map_not_a_crash(monkeypatch):
    def boom(code, session=None):
        raise OSError("network down")

    monkeypatch.setattr("eudamed.reference._get_csv", boom)
    maps = ReferenceMaps.load(cache=None)
    assert maps.risk_class("-204") == "-204"


def test_a_corrupt_cache_falls_back_to_fetching(tmp_path, monkeypatch):
    """A process killed mid-write can leave a truncated cache file. That must
    read as 'no cache' rather than crash every subsequent load()."""
    monkeypatch.setattr("eudamed.reference._get_csv", lambda code, session=None: CSV)
    cache = tmp_path / "reference_values.json"
    cache.write_text("{not valid json at all", encoding="utf-8")

    maps = ReferenceMaps.load(cache=cache)

    assert maps.risk_class("-204") == "Class IIa"
    assert maps.risk_class("-205") == "Class IIb"


def test_a_failed_fetch_does_not_poison_the_cache(tmp_path, monkeypatch):
    """A transient outage on a fresh build must not permanently write 'no
    data' to the cache -- that would make every later load() decode risk
    classes as raw integers forever, which is exactly what this module exists
    to prevent."""
    cache = tmp_path / "reference_values.json"

    def boom(code, session=None):
        raise OSError("network down")

    monkeypatch.setattr("eudamed.reference._get_csv", boom)
    failed = ReferenceMaps.load(cache=cache)

    assert not cache.exists()
    assert failed.risk_class("-204") == "-204"

    monkeypatch.setattr("eudamed.reference._get_csv", lambda code, session=None: CSV)
    recovered = ReferenceMaps.load(cache=cache)

    assert recovered.risk_class("-204") == "Class IIa"


def test_a_successful_fetch_still_writes_the_cache(tmp_path, monkeypatch):
    """Regression guard for the two fixes above: a clean fetch must still be
    cached, and a second load() must still avoid re-fetching."""
    calls = []

    def fake(code, session=None):
        calls.append(code)
        return CSV

    monkeypatch.setattr("eudamed.reference._get_csv", fake)
    cache = tmp_path / "reference_values.json"

    ReferenceMaps.load(cache=cache)
    assert cache.exists()
    first = len(calls)

    maps = ReferenceMaps.load(cache=cache)
    assert len(calls) == first
    assert maps.risk_class("-204") == "Class IIa"


def test_reference_body_is_decoded_as_utf8_despite_the_missing_charset_header(
    fake_session,
):
    """Same endpoint family as the Data Lake: UTF-8 served as bare text/csv."""
    from eudamed.reference import _get_csv

    body = "ID,CODE,LANGUAGE,VALUE\n-204,RISK_CLASS_ID,fr,Classe IIa — élevée\n"
    fake_session.queue(FakeResponse(
        200, content=body.encode("utf-8"),
        text=body.encode("utf-8").decode("iso-8859-1"),
        headers={"Content-Type": "text/csv"},
    ))
    assert _get_csv("RISK_CLASS_ID") == body
    assert fake_session.calls[0]["params"]["LANGUAGE"] == "en"
