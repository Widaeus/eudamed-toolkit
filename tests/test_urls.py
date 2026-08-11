"""Tests for EUDAMED deep-link construction.

Run: ./.venv/bin/python -m pytest tests/test_urls.py -q
"""

from __future__ import annotations

import pytest

from eudamed import urls


def test_device_url_uses_the_uuid_route():
    """The SPA declares one device route, `search-device/:uuid`, keyed on the
    UDI-DI uuid. The previous `?basicUdi=` form was a search-form parameter and
    the search route discards its query parameters on load."""
    u = urls.device_url("8dc77343-25b8-493b-b7f7-5c2bdebcf6b1")
    assert u == ("https://ec.europa.eu/tools/eudamed/#/screen/"
                 "search-device/8dc77343-25b8-493b-b7f7-5c2bdebcf6b1")
    assert "?" not in u


def test_actor_url_uses_the_uuid_route_not_the_srn():
    """`search-eo/:uuid` takes the actor uuid. An SRN in that slot resolves to
    nothing, which is the mistake this test exists to prevent."""
    assert urls.actor_url("cf896e78-4ab3-4a54-b668-0197b6ea019a") == (
        "https://ec.europa.eu/tools/eudamed/#/screen/"
        "search-eo/cf896e78-4ab3-4a54-b668-0197b6ea019a")


@pytest.mark.parametrize("bad", ["", None, "   ", "FR-MF-000000687"])
def test_url_builders_reject_anything_that_is_not_a_uuid(bad):
    """A blank or an SRN must raise rather than produce a link that 302s to the
    page-not-found route. A dead link in a reviewer workbook is worse than a
    missing one: the reviewer records 'cannot determine' against a broken URL."""
    with pytest.raises(ValueError):
        urls.device_url(bad)
    with pytest.raises(ValueError):
        urls.actor_url(bad)


def test_device_search_url_encodes_filters():
    u = urls.device_search_url(basicUdi="6429811134LapsiScribeG5")
    assert u.startswith("https://ec.europa.eu/tools/eudamed/#/screen/search-device?")
    assert "basicUdi=6429811134LapsiScribeG5" in u


def test_representative_uuid_prefers_the_latest_version():
    records = [
        {"uuid": "aaaaaaaa-0000-0000-0000-000000000000",
         "latestVersion": False, "versionNumber": 9},
        {"uuid": "bbbbbbbb-0000-0000-0000-000000000000",
         "latestVersion": True, "versionNumber": 1},
    ]
    assert urls.representative_uuid(records) == "bbbbbbbb-0000-0000-0000-000000000000"


def test_representative_uuid_falls_back_to_the_highest_version_number():
    records = [
        {"uuid": "aaaaaaaa-0000-0000-0000-000000000000",
         "latestVersion": False, "versionNumber": 2},
        {"uuid": "bbbbbbbb-0000-0000-0000-000000000000",
         "latestVersion": False, "versionNumber": 7},
    ]
    assert urls.representative_uuid(records) == "bbbbbbbb-0000-0000-0000-000000000000"


def test_representative_uuid_is_deterministic_under_a_full_tie():
    """Reviewers keep these links for months. The same device must produce the
    same URL on every rebuild, so ties break lexically rather than on input
    order."""
    a = {"uuid": "bbbbbbbb-0000-0000-0000-000000000000", "latestVersion": True,
         "versionNumber": 1}
    b = {"uuid": "aaaaaaaa-0000-0000-0000-000000000000", "latestVersion": True,
         "versionNumber": 1}
    assert urls.representative_uuid([a, b]) == urls.representative_uuid([b, a])
    assert urls.representative_uuid([a, b]) == "aaaaaaaa-0000-0000-0000-000000000000"


def test_representative_uuid_ignores_records_with_no_uuid():
    records = [{"uuid": None, "latestVersion": True, "versionNumber": 5},
               {"uuid": "cccccccc-0000-0000-0000-000000000000",
                "latestVersion": False, "versionNumber": 1}]
    assert urls.representative_uuid(records) == "cccccccc-0000-0000-0000-000000000000"


def test_representative_uuid_returns_none_when_there_is_nothing_to_choose():
    assert urls.representative_uuid([]) is None
    assert urls.representative_uuid([{"uuid": None}]) is None


def test_missing_version_fields_do_not_crash_the_ordering():
    """Discovery records from the legacy arm omit `versionNumber`."""
    records = [{"uuid": "dddddddd-0000-0000-0000-000000000000"}]
    assert urls.representative_uuid(records) == "dddddddd-0000-0000-0000-000000000000"
