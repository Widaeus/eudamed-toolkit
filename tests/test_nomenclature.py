"""Tests for EMDN traversal and code sweeps."""

from __future__ import annotations

import pytest

from eudamed.client import EudamedClient
from eudamed.errors import RequestFailed
from eudamed.nomenclature import sweep, terminal_codes, walk

from .conftest import FakeResponse


def test_terminal_codes_filters_by_suffix():
    nodes = [{"code": "Z1203", "term": "branch"},
             {"code": "Z120392", "term": "MEDICAL DEVICE SOFTWARE"},
             {"code": "Z120401", "term": "other"}]
    assert [n["code"] for n in terminal_codes(nodes, suffix="92")] == ["Z120392"]


def test_terminal_codes_returns_everything_without_a_suffix():
    nodes = [{"code": "A"}, {"code": "B"}]
    assert len(terminal_codes(nodes)) == 2


class _CountingClient:
    def __init__(self, counts):
        self.counts = counts
        self.calls = []

    def count_devices(self, **filters):
        self.calls.append(filters)
        return self.counts.get(filters.get("cndCode"), 0)


def test_sweep_counts_devices_per_code():
    client = _CountingClient({"Z120392": 412, "Z120393": 0})
    rows = sweep(client, ["Z120392", "Z120393"])
    assert rows == [{"code": "Z120392", "udi_di": 412},
                    {"code": "Z120393", "udi_di": 0}]


def test_sweep_skips_extra_counts_for_empty_codes():
    """A code with no devices cannot have any devices matching a sub-filter.
    Asking anyway spends a request per empty code against a service that
    throttles, and there are 173 software codes alone."""
    client = _CountingClient({"Z120392": 412, "Z120393": 0})
    rows = sweep(client, ["Z120392", "Z120393"],
                 legacy={"deviceCriteria": "LEGACY"})
    assert rows[0]["legacy"] == 412
    assert rows[1]["legacy"] == 0
    assert sum(1 for c in client.calls if "deviceCriteria" in c) == 1


class _TreeClient:
    """A fake nomenclature source: uuid -> list of child node dicts."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def nomenclature_children(self, cnd_uuid):
        self.calls.append(cnd_uuid)
        return self.tree.get(cnd_uuid, [])


def test_walk_traverses_breadth_first_with_depth():
    tree = {
        None: [{"uuid": "z", "code": "Z"}],
        "z": [{"uuid": "z1", "code": "Z1"}, {"uuid": "z2", "code": "Z2"}],
        "z1": [{"uuid": "z12", "code": "Z12"}],
    }
    client = _TreeClient(tree)
    nodes = list(walk(client))
    assert [(n["code"], n["depth"]) for n in nodes] == [
        ("Z", 1), ("Z1", 2), ("Z2", 2), ("Z12", 3),
    ]


def test_walk_starts_from_a_given_root():
    tree = {"z12": [{"uuid": "z1203", "code": "Z1203"}]}
    client = _TreeClient(tree)
    nodes = list(walk(client, root_uuid="z12"))
    assert [n["code"] for n in nodes] == ["Z1203"]
    assert client.calls[0] == "z12"


def test_walk_guards_against_cycles():
    """A node that (incorrectly) lists an ancestor as its own child must not
    send the walk into an infinite loop."""
    tree = {
        None: [{"uuid": "a", "code": "A"}],
        "a": [{"uuid": "b", "code": "B"}],
        "b": [{"uuid": "a", "code": "A"}],  # cycles back to the root's child
    }
    client = _TreeClient(tree)
    nodes = list(walk(client, max_depth=10))
    assert [n["code"] for n in nodes] == ["A", "B"]


def test_walk_stops_expanding_past_max_depth():
    tree = {
        None: [{"uuid": "a", "code": "A"}],
        "a": [{"uuid": "b", "code": "B"}],
        "b": [{"uuid": "c", "code": "C"}],
    }
    client = _TreeClient(tree)
    nodes = list(walk(client, max_depth=2))
    # B sits at the depth limit: it is yielded but never expanded, so its
    # child C is never fetched or seen.
    assert [n["code"] for n in nodes] == ["A", "B"]
    assert "b" not in client.calls


class _PayloadClient:
    """A fake nomenclature source returning an arbitrary raw payload per id,
    for exercising shape handling rather than tree structure."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def nomenclature_children(self, cnd_uuid):
        self.calls.append(cnd_uuid)
        return self.payloads.get(cnd_uuid)


def test_walk_accepts_children_keyed_cnd_uuid():
    """The endpoint's own path parameter is named 'cndUuid'
    (GET /devices/nomenclatures/{cndUuid}/children); a response that uses
    that name for the child's id must not be silently dropped."""
    payloads = {None: [{"cndUuid": "z", "code": "Z"}], "z": []}
    client = _PayloadClient(payloads)
    assert list(walk(client)) == [{"cndUuid": "z", "code": "Z", "depth": 1}]


def test_walk_raises_on_a_node_with_no_recognisable_id_key():
    payloads = {None: [{"code": "Z", "term": "branch"}]}
    client = _PayloadClient(payloads)
    with pytest.raises(ValueError) as excinfo:
        list(walk(client))
    assert "code" in str(excinfo.value)
    assert "term" in str(excinfo.value)


def test_walk_accepts_a_paged_content_envelope():
    """The device search endpoint's page envelope -- {"content": [...]} -- is
    the likely alternative shape for this endpoint too."""
    payloads = {None: {"content": [{"uuid": "z", "code": "Z"}], "totalElements": 1}}
    client = _PayloadClient(payloads)
    assert list(walk(client)) == [{"uuid": "z", "code": "Z", "depth": 1}]


def test_walk_accepts_a_children_envelope():
    payloads = {None: {"children": [{"uuid": "z", "code": "Z"}]}}
    client = _PayloadClient(payloads)
    assert list(walk(client)) == [{"uuid": "z", "code": "Z", "depth": 1}]


def test_walk_raises_type_error_on_an_unrecognised_payload_shape():
    payloads = {None: "oops"}
    client = _PayloadClient(payloads)
    with pytest.raises(TypeError) as excinfo:
        list(walk(client))
    assert "str" in str(excinfo.value)
    assert "oops" in str(excinfo.value)


def test_walk_raises_type_error_on_a_dict_without_a_known_list_key():
    payloads = {None: {"totalElements": 0}}
    client = _PayloadClient(payloads)
    with pytest.raises(TypeError) as excinfo:
        list(walk(client))
    assert "totalElements" in str(excinfo.value)


def test_walk_raises_when_the_endpoint_is_down(tmp_path, fake_session):
    """I2, and not a hypothetical: GET /devices/nomenclatures/ answered HTTP
    500 to every form tried, so this is the path a live walk
    actually takes. Printing `[]` and exiting 0 states that the EMDN tree is
    empty, which the module's own docstring promises never to do."""
    client = EudamedClient(cache_dir=tmp_path / "cache",
                           run_log=tmp_path / "requests.jsonl",
                           min_interval=0.0, max_retries=2)
    fake_session.queue(FakeResponse(500), FakeResponse(500))
    with pytest.raises(RequestFailed) as excinfo:
        list(walk(client))
    assert excinfo.value.status == 500
    assert "nomenclatures" in excinfo.value.url


def test_sweep_raises_when_a_count_cannot_be_taken(tmp_path, fake_session):
    """The compounding case: a code whose count failed would otherwise be
    recorded as zero devices, and every sub-count under it recorded as zero
    without a request ever being made."""
    client = EudamedClient(cache_dir=tmp_path / "cache",
                           run_log=tmp_path / "requests.jsonl",
                           min_interval=0.0, max_retries=1)
    fake_session.queue(FakeResponse(503))
    with pytest.raises(RequestFailed):
        sweep(client, ["Z120392"], legacy={"deviceCriteria": "LEGACY"})
