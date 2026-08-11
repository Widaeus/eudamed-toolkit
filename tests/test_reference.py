# tests/test_reference.py
"""Tests for reference-value decoding."""

from __future__ import annotations

from eudamed.reference import ReferenceMaps

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
