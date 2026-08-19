"""Tests for the public read API client."""

from __future__ import annotations

import pytest

from eudamed.client import VERIFIED_DEVICE_FILTERS, EudamedClient
from eudamed.errors import RequestFailed

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
    silently replaces your result set with the whole register -- over three
    million records. Anything that weakens this check should fail here."""
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


def test_iter_devices_asks_for_no_page_after_the_last_one(tmp_path, fake_session):
    """`last: true` is the register's own end-of-results marker and must be
    honoured. Walking past it to discover emptiness spends a request per
    crawl against a service that throttles, and -- worse -- makes the crawl
    depend on a page beyond the end answering successfully, so a 503 there
    would turn a completed crawl into a failed one."""
    client = _client(tmp_path)
    fake_session.queue(
        FakeResponse(200, {"content": [{"uuid": "a"}], "last": True}),
        FakeResponse(503),
    )
    assert [r["uuid"] for r in client.iter_devices(page_size=1)] == ["a"]
    assert len(fake_session.calls) == 1


def test_iter_devices_raises_when_a_page_fails_rather_than_truncating(tmp_path, fake_session):
    """C1. Page 1 arrives, page 2 is a 503. Treating that as the end of the
    result set writes a partial extract that looks complete, manifest and
    SHA-256 and all, with nothing anywhere recording that a page is missing."""
    client = _client(tmp_path, max_retries=2)
    fake_session.queue(
        FakeResponse(200, {"content": [{"uuid": "a"}], "last": False}),
        FakeResponse(503),
        FakeResponse(503),
    )
    with pytest.raises(RequestFailed) as excinfo:
        list(client.iter_devices(page_size=1))
    assert excinfo.value.status == 503
    assert excinfo.value.attempts == 2
    assert "udiDiData" in excinfo.value.url


def test_iter_devices_returns_nothing_for_a_genuinely_empty_result(tmp_path, fake_session):
    """The other half of C1: a filter matching no devices is a real answer and
    must stay an ordinary empty iteration, not an error."""
    client = _client(tmp_path)
    fake_session.queue(FakeResponse(200, {"content": [], "totalElements": 0, "last": True}))
    assert list(client.iter_devices(page_size=1, cndCode="Z999999")) == []


def test_count_devices_raises_instead_of_reporting_zero_on_failure(tmp_path, fake_session):
    """C2. `eudamed search --count` printing 0 during an outage is a claim
    about the register that nobody checked. Any failure sentinel here -- 0,
    -1, None -- is a wrong answer rather than no answer."""
    client = _client(tmp_path, max_retries=2)
    fake_session.queue(FakeResponse(503), FakeResponse(503))
    with pytest.raises(RequestFailed) as excinfo:
        client.count_devices(cndCode="Z1203")
    assert excinfo.value.status == 503
    assert excinfo.value.params["cndCode"] == "Z1203"


def test_count_devices_reports_a_real_zero_as_zero(tmp_path, fake_session):
    client = _client(tmp_path)
    fake_session.queue(FakeResponse(200, {"content": [], "totalElements": 0}))
    assert client.count_devices(cndCode="Z999999") == 0


def test_a_non_json_200_is_a_failure_not_an_empty_record(tmp_path, fake_session):
    """The API's signature failure mode is a 200 that is not what it claims to
    be. An HTML error page parsed as 'no data' is exactly the corruption this
    client refuses elsewhere."""
    client = _client(tmp_path, max_retries=1)
    fake_session.queue(FakeResponse(200, payload=None, content=b"<html>oops</html>"))
    with pytest.raises(RequestFailed, match="not JSON"):
        client.get("devices/udiDiData/abc", {})


def test_the_user_agent_identifies_the_tool_and_a_contact(tmp_path):
    client = _client(tmp_path, contact="someone@example.org")
    agent = client.session.headers["User-Agent"]
    assert agent.startswith("eudamed-toolkit/")
    assert "someone@example.org" in agent
