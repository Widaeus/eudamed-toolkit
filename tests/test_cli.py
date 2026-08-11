"""Tests for the command-line interface."""

from __future__ import annotations

import json

import pytest

from eudamed.cli import _kebab, main
from eudamed.client import VERIFIED_DEVICE_FILTERS
from eudamed.errors import RequestFailed


def test_no_arguments_prints_usage_and_fails(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_version_is_reported(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "eudamed-toolkit" in capsys.readouterr().out


def test_search_prints_the_total(monkeypatch, capsys):
    class _Client:
        def __init__(self, **kw):
            pass

        def count_devices(self, **filters):
            assert filters == {"cndCode": "Z12"}
            return 206769

    monkeypatch.setattr("eudamed.cli.EudamedClient", _Client)
    assert main(["search", "--cnd-code", "Z12", "--count"]) == 0
    assert "206769" in capsys.readouterr().out.replace(",", "")


def test_no_cache_stops_the_client_being_given_a_cache_directory(monkeypatch, capsys):
    """The register changes daily. Without a way to turn the cache off, a
    re-run can serve last month's records with nothing in the output saying
    so."""
    captured = {}

    class _Client:
        def __init__(self, **kw):
            captured.update(kw)

        def count_devices(self, **filters):
            return 1

    monkeypatch.setattr("eudamed.cli.EudamedClient", _Client)
    assert main(["search", "--count", "--no-cache", "--cnd-code", "Z12"]) == 0
    assert captured["cache_dir"] is None

    assert main(["search", "--count", "--cnd-code", "Z12"]) == 0
    assert captured["cache_dir"] == "data/raw/.cache"


def test_a_failed_request_exits_non_zero_with_its_own_code(monkeypatch, capsys):
    """`--count` printing 0 and exiting 0 during an outage is a false answer.
    Exit 3 is distinct from 1 (no such record) so a script can tell 'nothing
    is there' from 'we could not find out'."""

    class _Client:
        def __init__(self, **kw):
            pass

        def count_devices(self, **filters):
            raise RequestFailed(
                "https://ec.europa.eu/tools/eudamed/api/devices/udiDiData",
                filters, status=503, attempts=8,
            )

    monkeypatch.setattr("eudamed.cli.EudamedClient", _Client)
    assert main(["search", "--count", "--cnd-code", "Z1203"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "503" in captured.err


def test_an_unverified_filter_is_refused_before_any_request(monkeypatch, capsys):
    """argparse must not accept a filter the client would reject, and no request
    should be made to find that out."""
    with pytest.raises(SystemExit):
        main(["search", "--manufacturer-name", "Siemens"])


def test_device_prints_the_record_and_its_link(monkeypatch, capsys):
    class _Client:
        def __init__(self, **kw):
            pass

        def device_detail(self, uuid):
            return {"uuid": uuid, "tradeName": "Widget"}

    monkeypatch.setattr("eudamed.cli.EudamedClient", _Client)
    uuid = "8dc77343-25b8-493b-b7f7-5c2bdebcf6b1"
    assert main(["device", uuid]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.split("\n\n")[0])["tradeName"] == "Widget"
    assert f"/screen/search-device/{uuid}" in out


def test_actor_prints_the_record_and_its_link(monkeypatch, capsys):
    class _Client:
        def __init__(self, **kw):
            pass

        def actor(self, uuid):
            return {"uuid": uuid, "name": "Acme Devices"}

    monkeypatch.setattr("eudamed.cli.EudamedClient", _Client)
    uuid = "8dc77343-25b8-493b-b7f7-5c2bdebcf6b1"
    assert main(["actor", uuid]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.split("\n\n")[0])["name"] == "Acme Devices"
    assert f"/screen/search-eo/{uuid}" in out


def test_a_flag_exists_for_every_verified_filter(capsys):
    """The allow-list and the CLI surface must never drift apart: a filter
    missing its flag here would be silently unreachable, and one added by hand
    instead of generated could reach the client unverified."""
    with pytest.raises(SystemExit):
        main(["search", "--help"])
    out = capsys.readouterr().out
    for name in VERIFIED_DEVICE_FILTERS:
        assert f"--{_kebab(name)}" in out


def test_export_passes_its_filters_through_to_export_devices(monkeypatch, tmp_path):
    captured = {}

    class _Client:
        def __init__(self, **kw):
            pass

    def _fake_export_devices(client, out, fmt="jsonl", enrich=False, progress=None, **filters):
        captured["out"] = out
        captured["fmt"] = fmt
        captured["enrich"] = enrich
        captured["filters"] = filters
        return {"records": 0, "path": str(out), "manifest": "manifest.json", "filters": filters}

    monkeypatch.setattr("eudamed.cli.EudamedClient", _Client)
    monkeypatch.setattr("eudamed.cli.export_devices", _fake_export_devices)

    out_path = tmp_path / "out.jsonl"
    result = main(
        [
            "export",
            str(out_path),
            "--cnd-code",
            "Z12",
            "--trade-name",
            "Widget",
            "--enrich",
        ]
    )
    assert result == 0
    assert captured["filters"] == {"cndCode": "Z12", "tradeName": "Widget"}
    assert captured["fmt"] == "jsonl"
    assert captured["enrich"] is True
    assert captured["out"] == out_path
