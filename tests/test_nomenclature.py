"""Tests for EMDN traversal and code sweeps."""

from __future__ import annotations

from eudamed.nomenclature import sweep, terminal_codes, walk


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
