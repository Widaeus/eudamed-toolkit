"""Tests for the public read API client."""

from __future__ import annotations

import pytest

from eudamed.client import VERIFIED_DEVICE_FILTERS, EudamedClient

from .conftest import FakeResponse


def _client(tmp_path, **kw):
    kw.setdefault("min_interval", 0.0)
    return EudamedClient(cache_dir=tmp_path / "cache",
                         run_log=tmp_path / "requests.jsonl",
                         **kw)


def test_an_unverified_filter_raises(tmp_path):
    """The single most important test in this package.

    The API ignores query parameters it does not recognise and returns the whole
    register with HTTP 200. A misspelled filter therefore does not fail, it
    silently replaces your result set with 2.98 million records. Anything that
    weakens this check should fail here."""
    client = _client(tmp_path)
    with pytest.raises(ValueError, match="unverified"):
        client.search_devices(manufacturerName="Siemens")


def test_the_allow_list_holds_only_filters_that_were_measured():
    """Each of these was verified by observing that it changes totalElements.
    Adding a name without that measurement reintroduces the failure above."""
    assert VERIFIED_DEVICE_FILTERS == frozenset({
        "cndCode", "riskClassCode", "deviceStatusCode", "applicableLegislation",
        "tradeName", "name", "primaryDi", "basicUdi", "deviceTypes",
        "deviceCriteria",
    })


def test_a_redirect_is_a_miss_not_an_error(tmp_path, fake_session):
    """EUDAMED answers 'no such record' with a 302 to its page-not-found route,
    never a 404. Following it yields an HTML page with a 200 status."""
    fake_session.queue(FakeResponse(302))
    assert _client(tmp_path).device_detail("11111111-0000-0000-0000-000000000000") is None
    assert fake_session.calls[0]["allow_redirects"] is False


def test_a_429_widens_the_interval_for_the_rest_of_the_run(tmp_path, fake_session):
    """A throttle means the chosen rate was wrong, not that one request was
    unlucky. Retrying at the same rate earns another 429."""
    client = _client(tmp_path, min_interval=0.01)
    fake_session.queue(FakeResponse(429), FakeResponse(200, {"ok": True}))
    before = client.min_interval
    assert client.get("devices/udiDiData", {"page": 0}) == {"ok": True}
    assert client.min_interval > before


def test_responses_are_cached_on_disk(tmp_path, fake_session):
    client = _client(tmp_path)
    fake_session.queue(FakeResponse(200, {"uuid": "abc"}))
    first = client.get("devices/udiDiData/abc", {"languageIso2Code": "en"})
    second = client.get("devices/udiDiData/abc", {"languageIso2Code": "en"})
    assert first == second == {"uuid": "abc"}
    assert len(fake_session.calls) == 1


def test_cache_can_be_disabled(tmp_path, fake_session):
    """A one-off extraction should not have to leave a cache directory behind."""
    client = EudamedClient(cache_dir=None, run_log=tmp_path / "r.jsonl", min_interval=0.0)
    fake_session.queue(FakeResponse(200, {"a": 1}), FakeResponse(200, {"a": 2}))
    assert client.get("x", {}) == {"a": 1}
    assert client.get("x", {}) == {"a": 2}


def test_every_request_is_logged(tmp_path, fake_session):
    client = _client(tmp_path)
    fake_session.queue(FakeResponse(200, {"ok": True}))
    client.get("devices/udiDiData", {"page": 0})
    lines = (tmp_path / "requests.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert '"status": 200' in lines[0]


def test_iter_devices_stops_at_the_last_page(tmp_path, fake_session):
    client = _client(tmp_path)
    fake_session.queue(
        FakeResponse(200, {"content": [{"uuid": "a"}], "last": False}),
        FakeResponse(200, {"content": [{"uuid": "b"}], "last": True}),
    )
    assert [r["uuid"] for r in client.iter_devices(page_size=1)] == ["a", "b"]


def test_the_user_agent_identifies_the_tool_and_a_contact(tmp_path):
    client = _client(tmp_path, contact="someone@example.org")
    agent = client.session.headers["User-Agent"]
    assert agent.startswith("eudamed-toolkit/")
    assert "someone@example.org" in agent
