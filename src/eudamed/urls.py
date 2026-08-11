"""Construction of links into the public EUDAMED interface.

Every EUDAMED URL used anywhere in this project is built here. The public
interface is an Angular single-page application, so its addresses are hash
routes and cannot be inferred from the REST API paths. The route table was read
out of the deployed bundle (`main.<hash>.js` and the lazy feature chunks listed
in `runtime.<hash>.js`) and confirmed against the live service on 2026-08-11:

    screen/search-device/:uuid    the UDI-DI uuid
    screen/search-eo/:uuid        the actor uuid

Both routes take uuids. Neither accepts a Basic UDI-DI code, a primary DI or an
SRN, and the search route discards its query parameters on load, so
`search-device?basicUdi=...` does not open a device.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlencode

SITE_BASE = "https://ec.europa.eu/tools/eudamed"

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _require_uuid(value: Any, kind: str) -> str:
    """A blank or an SRN here would produce a link that redirects to the
    page-not-found route. A reviewer cannot distinguish that from a device with
    no public record, so it has to fail at construction time."""
    text = str(value).strip() if value is not None else ""
    if not _UUID.match(text):
        raise ValueError(f"{kind} must be a uuid, got {value!r}")
    return text.lower()


def device_url(udi_di_uuid: str) -> str:
    """Link to a device record, keyed on the UDI-DI uuid."""
    return f"{SITE_BASE}/#/screen/search-device/{_require_uuid(udi_di_uuid, 'device uuid')}"


def actor_url(actor_uuid: str) -> str:
    """Link to an economic operator, keyed on the actor uuid.

    The actor uuid is not in any search response; it is read from
    ``manufacturer.uuid`` on the Basic UDI-DI detail record.
    """
    return f"{SITE_BASE}/#/screen/search-eo/{_require_uuid(actor_uuid, 'actor uuid')}"


def device_search_url(**filters: str) -> str:
    """Link to the device search screen with its form pre-filled.

    Useful for a human retracing a query. It does not open a device record.
    """
    query = urlencode({k: v for k, v in filters.items() if v is not None})
    return f"{SITE_BASE}/#/screen/search-device" + (f"?{query}" if query else "")


def representative_uuid(records: Iterable[Mapping[str, Any]]) -> str | None:
    """Choose the UDI-DI a Basic UDI-DI's link should point at.

    A Basic UDI-DI carries several UDI-DIs — the observed inflation factor is
    about 2.5 — so one has to be chosen. Reviewers keep these links for months,
    so the choice is deterministic rather than dependent on input order: the
    latest version, then the highest version number, then the lexically first
    uuid.
    """
    best: tuple[bool, int, str] | None = None
    for record in records:
        raw = record.get("uuid")
        if not raw:
            continue
        uuid = str(raw)
        key = (bool(record.get("latestVersion")), int(record.get("versionNumber") or 0))
        # The first two components sort descending (later version wins) and the
        # third ascending (lexically first uuid wins), so the comparison is
        # written out rather than done with a single sort key.
        if best is None or (key[0], key[1]) > (best[0], best[1]) or (
            (key[0], key[1]) == (best[0], best[1]) and uuid < best[2]
        ):
            best = (key[0], key[1], uuid)
    return best[2] if best else None
