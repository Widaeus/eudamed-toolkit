"""Decode EUDAMED's reference-value IDs into English labels.

Risk class, applicable legislation and market status are stored in the
register as opaque integer IDs. Those IDs are never guessed: an earlier
attempt guessed them and was wrong on every one — Class IIa is ``-204``, not
``-13`` — and because nothing validated the guess, the wrong integers were
written straight into a human-facing column without anything failing. This
module fetches the register's own ``/reference`` endpoint instead.

The endpoint returns every language in one response and caps at 1,000 rows,
so it is queried one CODE at a time and filtered to English.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import requests

REFERENCE_URL = "https://api.datalake.sante.service.ec.europa.eu/eudamed/reference"

RISK_CLASS_ID = "RISK_CLASS_ID"
DEVICE_STATUS_TYPE_ID = "DEVICE_STATUS_TYPE_ID"
APPLICABLE_LEGISLATION_ID = "APPLICABLE_LEGISLATION_ID"
REFERENCE_CODES = (RISK_CLASS_ID, DEVICE_STATUS_TYPE_ID, APPLICABLE_LEGISLATION_ID)

log = logging.getLogger("eudamed.reference")


def _get_csv(code: str, session: requests.Session | None = None) -> str:
    """Issue one GET for a single reference CODE and return the response body.

    Isolated from `ReferenceMaps.load` so tests can replace it without a
    network call.
    """
    http = session or requests
    resp = http.get(
        REFERENCE_URL,
        timeout=60,
        params={"api-version": "v1.0", "format": "csv", "CODE": code},
    )
    resp.raise_for_status()
    return resp.text


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
    def load(cls, cache: Path | None = None) -> ReferenceMaps:
        """Load {CODE: {id: english label}}, cached so a rebuild works offline.

        A fetch failure yields an empty map for that code rather than raising,
        so a rebuild from cache works even when the register is unreachable.
        """
        if cache is not None and cache.exists():
            return cls(json.loads(cache.read_text(encoding="utf-8")))

        maps: dict[str, dict[str, str]] = {}
        for code in REFERENCE_CODES:
            try:
                maps[code] = _parse_english(_get_csv(code))
                log.info("reference %s: %d values", code, len(maps[code]))
            except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                log.warning("could not fetch reference %s: %s", code, exc)
                maps[code] = {}

        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(maps, indent=1), encoding="utf-8")

        return cls(maps)
