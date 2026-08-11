"""Traverse the EMDN nomenclature tree and count devices per code.

The European Medical Device Nomenclature (EMDN) is the classification tree
EUDAMED uses to categorise devices. Codes are hierarchical strings (``Z12``,
``Z1203``, ``Z120392``); the register's ``cndCode`` filter is a **prefix
match**, so a branch code returns every device registered anywhere under it.
Terminal codes carry meaning in their suffix -- ``...92`` marks software, for
instance -- which is why callers filter on it rather than on the tree
structure itself.

``walk`` expects a ``client`` with a ``nomenclature_children(uuid)`` method
returning the direct children of a node, or an empty/``None`` result for a
node with no children. The exact response shape (bare list, a paged envelope
under ``content``, or a ``children`` wrapper) has not been confirmed against
a live response, so ``walk`` normalises all three explicitly and raises
``TypeError`` naming whatever else it is given, rather than silently treating
an unrecognised shape as "no children". Each child node must carry an id
under ``uuid``, ``cndUuid`` or ``id``; a node with none of those raises
``ValueError`` naming the keys it does have, rather than being dropped.
``sweep`` expects a ``client`` with a ``count_devices(**filters)`` method
returning an integer.

**Status of the underlying endpoint.** ``GET /devices/nomenclatures/``
returned HTTP 500 for every form tried on 2026-08-11 (see
``docs/api-reference.md``), so a live traversal currently fails rather than
succeeding. That failure propagates out of ``walk`` as ``RequestFailed``: an
outage must not be indistinguishable from a tree with no nodes, which is
precisely what returning an empty list would make it. The shape handling
above is therefore written against the endpoint's documented contract and
remains unverified against a live response.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

_ID_KEYS = ("uuid", "cndUuid", "id")


def _node_id(node: Mapping[str, Any]) -> Any:
    """The node's identifier, tried under each plausible key in turn.

    Guessing wrong here must never look like "this node has no children" --
    that is indistinguishable from an empty branch. A node carrying none of
    the known id keys raises instead of being silently skipped.
    """
    for key in _ID_KEYS:
        if key in node:
            return node[key]
    raise ValueError(
        f"EMDN node has no recognisable id key (tried {_ID_KEYS!r}); "
        f"node has keys {sorted(node.keys())!r}"
    )


def _children(payload: Any) -> list[Mapping[str, Any]]:
    """Normalise a ``nomenclature_children`` response to a list of nodes.

    Three shapes are accepted: a bare list, a paged envelope
    ``{"content": [...]}`` (the shape the device search endpoint uses, and
    so the likely alternative), and ``{"children": [...]}``. Anything else
    raises ``TypeError`` naming what was received -- an unrecognised shape
    must fail loudly, not be read as an empty branch.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("content", "children"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        raise TypeError(
            "nomenclature_children returned a dict with neither a 'content' "
            f"nor a 'children' list: keys={sorted(payload.keys())!r}"
        )
    raise TypeError(
        f"nomenclature_children returned an unrecognised shape: "
        f"{type(payload).__name__} ({payload!r})"
    )


def walk(
    client: Any, root_uuid: str | None = None, max_depth: int = 8
) -> Iterator[dict[str, Any]]:
    """Breadth-first traversal of the tree below ``root_uuid``.

    Yields each descendant node (never the root itself) as a dict with a
    ``depth`` key added, starting at 1 for the root's direct children. A
    node's children are fetched only while its own depth is below
    ``max_depth``, so no yielded node exceeds that depth. A seen-set keyed on
    node id stops re-visiting a node reachable by more than one path -- the
    tree is assumed acyclic, but a malformed response must not be able to
    hang the walk. The set starts empty (or with just ``root_uuid``, if
    given): seeding it with a sentinel such as ``None`` would make every
    node lacking a recognised id key compare equal to that sentinel and be
    dropped as "already seen", which is exactly the silent-loss failure this
    function must not have.
    """
    seen: set[Any] = {root_uuid} if root_uuid is not None else set()
    queue: deque[tuple[Any, int]] = deque([(root_uuid, 0)])
    while queue:
        parent_uuid, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for node in _children(client.nomenclature_children(parent_uuid)):
            node_uuid = _node_id(node)
            if node_uuid in seen:
                continue
            seen.add(node_uuid)
            child_depth = depth + 1
            yield {**node, "depth": child_depth}
            queue.append((node_uuid, child_depth))


def terminal_codes(
    nodes: Iterable[Mapping[str, Any]], suffix: str | None = None
) -> list[dict[str, Any]]:
    """Nodes whose ``code`` ends in ``suffix``, or all of them if it is None."""
    return [dict(n) for n in nodes if suffix is None or str(n.get("code", "")).endswith(suffix)]


def sweep(
    client: Any, codes: Iterable[str], **extra_counts: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Count devices per EMDN code, plus any caller-named sub-filters.

    ``extra_counts`` maps a result column name to a set of extra filters
    (e.g. ``legacy={"deviceCriteria": "LEGACY"}``), added to the base
    ``cndCode`` filter for that code. A code with zero devices is skipped for
    every extra count rather than queried again: it cannot have devices
    matching a sub-filter, and a service that throttles hard should not pay
    for a request whose answer is already known.
    """
    rows = []
    for code in codes:
        base = client.count_devices(cndCode=code)
        row: dict[str, Any] = {"code": code, "udi_di": base}
        for name, filters in extra_counts.items():
            row[name] = client.count_devices(cndCode=code, **filters) if base else 0
        rows.append(row)
    return rows
