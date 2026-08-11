"""Traverse the EMDN nomenclature tree and count devices per code.

The European Medical Device Nomenclature (EMDN) is the classification tree
EUDAMED uses to categorise devices. Codes are hierarchical strings (``Z12``,
``Z1203``, ``Z120392``); the register's ``cndCode`` filter is a **prefix
match**, so a branch code returns every device registered anywhere under it.
Terminal codes carry meaning in their suffix -- ``...92`` marks software, for
instance -- which is why callers filter on it rather than on the tree
structure itself.

``walk`` expects a ``client`` with a ``nomenclature_children(uuid)`` method
that returns the direct children of a node as a list of mappings, each
carrying at least a ``uuid`` and a ``code``, or an empty/``None`` result for a
node with no children. ``sweep`` expects a ``client`` with a
``count_devices(**filters)`` method returning an integer.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from typing import Any


def walk(
    client: Any, root_uuid: str | None = None, max_depth: int = 8
) -> Iterator[dict[str, Any]]:
    """Breadth-first traversal of the tree below ``root_uuid``.

    Yields each descendant node (never the root itself) as a dict with a
    ``depth`` key added, starting at 1 for the root's direct children. A
    node's children are fetched only while its own depth is below
    ``max_depth``, so no yielded node exceeds that depth. A seen-set keyed on
    node ``uuid`` stops re-visiting a node reachable by more than one path --
    the tree is assumed acyclic, but a malformed response must not be able to
    hang the walk.
    """
    seen: set[Any] = {root_uuid}
    queue: deque[tuple[Any, int]] = deque([(root_uuid, 0)])
    while queue:
        parent_uuid, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for node in client.nomenclature_children(parent_uuid) or []:
            node_uuid = node.get("uuid")
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
