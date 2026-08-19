"""Decode EUDAMED's reference-value IDs into English labels.

Risk class, applicable legislation and market status are stored in the
register as opaque integer IDs. Those IDs are never guessed: an earlier
attempt guessed them and was wrong on every one — Class IIa is ``-204``, not
``-13`` — and because nothing validated the guess, the wrong integers were
written straight into a human-facing column without anything failing. This
module fetches the register's own ``/reference`` endpoint instead.

The endpoint caps at 1,000 rows and, unless asked, returns every language
in one response, so it is queried one CODE at a time with ``LANGUAGE=en``
and filtered to English again on the way in.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from eudamed import user_agent as _default_user_agent

REFERENCE_URL = "https://api.datalake.sante.service.ec.europa.eu/eudamed/reference"

RISK_CLASS_ID = "RISK_CLASS_ID"
DEVICE_STATUS_TYPE_ID = "DEVICE_STATUS_TYPE_ID"
APPLICABLE_LEGISLATION_ID = "APPLICABLE_LEGISLATION_ID"
REFERENCE_CODES = (RISK_CLASS_ID, DEVICE_STATUS_TYPE_ID, APPLICABLE_LEGISLATION_ID)

log = logging.getLogger("eudamed.reference")


def build_session(contact: str | None = None) -> requests.Session:
    """A session identifying itself as this package, with an optional contact.

    Calling ``requests.get`` directly sends the library's default User-Agent,
    which tells the Commission's logs nothing about who is making the request
    or how to reach them. Every other request this package makes is
    identified; these three were the exception.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": _default_user_agent(contact)})
    return session


def _get_csv(code: str, session: requests.Session | None = None) -> str:
    """Issue one GET for a single reference CODE and return the response body.

    Isolated from `ReferenceMaps.load` so tests can replace it without a
    network call.
    """
    http = session if session is not None else build_session()
    resp = http.get(
        REFERENCE_URL,
        timeout=60,
        # LANGUAGE narrows the response server-side; the English filter in
        # `_parse_english` stays as a guard should the parameter stop working.
        params={"api-version": "v1.0", "format": "csv", "CODE": code, "LANGUAGE": "en"},
    )
    resp.raise_for_status()
    # Served as bare ``text/csv`` with no charset, so ``resp.text`` would be
    # decoded as ISO-8859-1 and mangle every non-ASCII label. The body is UTF-8.
    return resp.content.decode("utf-8")


def _parse_english(csv_text: str) -> dict[str, str]:
    """{id: english label} from one CODE's CSV response.

    The response carries every language; keeping them all would make the last
    language written win, which is not a decision anyone made.
    """
    values: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("LANGUAGE", "en") != "en":
            continue
        values[row["ID"]] = row["VALUE"]
    return values


def _read_cache(cache: Path) -> dict[str, dict[str, str]] | None:
    """The cached maps, or ``None`` if the file is missing or unreadable.

    A process killed mid-write can leave a truncated file. That must read as
    "no cache" rather than raise, or a rebuild would need someone to delete
    the file by hand before it worked offline again.
    """
    try:
        loaded: dict[str, dict[str, str]] = json.loads(cache.read_text(encoding="utf-8"))
        return loaded
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("cache %s is unreadable (%s); refetching", cache, exc)
        return None


def _write_cache_atomic(cache: Path, maps: dict[str, dict[str, str]]) -> None:
    """Write ``maps`` to ``cache`` so a killed process can never leave a
    half-written file: write to a temp file in the same directory, then
    ``os.replace`` it into place, which is atomic on POSIX and Windows."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=cache.parent, prefix=cache.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(maps, indent=1))
        os.replace(tmp_name, cache)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


@dataclass
class ReferenceMaps:
    """English labels for the reference IDs EUDAMED never explains inline."""

    maps: dict[str, dict[str, str]] = field(default_factory=dict)

    def _lookup(self, code: str, value_id: str | int) -> str:
        """The English label for `value_id`, or the ID itself if unknown.

        A blank reads as "this device has no risk class", which is never
        true, so an unrecognised ID is returned unchanged rather than dropped.
        """
        value_id = str(value_id)
        return self.maps.get(code, {}).get(value_id, value_id)

    def risk_class(self, value_id: str | int) -> str:
        return self._lookup(RISK_CLASS_ID, value_id)

    def legislation(self, value_id: str | int) -> str:
        return self._lookup(APPLICABLE_LEGISLATION_ID, value_id)

    def status(self, value_id: str | int) -> str:
        return self._lookup(DEVICE_STATUS_TYPE_ID, value_id)

    @classmethod
    def load(cls, cache: Path | None = None, contact: str | None = None) -> ReferenceMaps:
        """Load {CODE: {id: english label}}, cached so a rebuild works offline.

        A fetch failure yields an empty map for that code rather than raising,
        so a rebuild from cache works even when the register is unreachable.
        That empty result is never itself cached: a transient outage on a
        fresh build must not permanently poison the cache with "no data" that
        every later `load()` would then treat as authoritative.
        """
        if cache is not None and cache.exists():
            cached = _read_cache(cache)
            if cached is not None:
                return cls(cached)

        maps: dict[str, dict[str, str]] = {}
        fetch_failed = False
        session = build_session(contact)
        for code in REFERENCE_CODES:
            try:
                maps[code] = _parse_english(_get_csv(code, session))
                log.info("reference %s: %d values", code, len(maps[code]))
            except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                log.warning("could not fetch reference %s: %s", code, exc)
                maps[code] = {}
                fetch_failed = True

        if cache is not None:
            if fetch_failed:
                log.warning(
                    "not writing cache %s: at least one reference fetch failed", cache
                )
            else:
                _write_cache_atomic(cache, maps)

        return cls(maps)
