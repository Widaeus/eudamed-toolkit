"""Rate-limited, logged client for the EUDAMED public API.

Endpoint names and query parameters were established empirically against the
live public API; it is undocumented by the Commission, and parameters not
listed in ``VERIFIED_DEVICE_FILTERS`` were verified to be inert.

Every request is appended to a JSONL run log so that an extraction can be
reconstructed and audited after the fact.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from eudamed import __version__

BASE = "https://ec.europa.eu/tools/eudamed/api"

# Filter parameters verified to change result counts against the live API.
# Parameters the UI exposes but which the backend ignores are not included
# here; see docs/api-reference.md so nobody re-tests them.
VERIFIED_DEVICE_FILTERS = frozenset({
    "cndCode",               # EMDN code, prefix match
    "riskClassCode",         # full refdata code, e.g. refdata.risk-class.class-iib
    "deviceStatusCode",      # full refdata code
    "applicableLegislation", # full refdata code
    "tradeName",             # substring, case-insensitive
    "name",                  # substring over a field that INCLUDES the manufacturer name
    "primaryDi",             # exact
    "basicUdi",              # exact
    "deviceTypes",           # special device type, e.g.
                             # refdata.special-mdr-device-type.software
    "deviceCriteria",        # STANDARD or LEGACY
})

log = logging.getLogger("eudamed.client")


@dataclass
class RunLog:
    """Append-only request log. One JSON object per request."""

    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class EudamedClient:
    """Polite, resumable client for the EUDAMED public read API.

    ``min_interval`` is enforced across threads, so raising ``workers`` raises
    concurrency but never the request rate.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = "data/raw/.cache",
        run_log: Path | str = "logs/requests.jsonl",
        min_interval: float = 0.4,
        max_interval: float = 4.0,
        recovery_after: int = 150,
        timeout: int = 120,
        max_retries: int = 8,
        user_agent: str | None = None,
        contact: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.run_log = RunLog(Path(run_log))
        self.min_interval = min_interval
        self._floor_interval = min_interval
        self.max_interval = max_interval
        self.recovery_after = recovery_after
        self._successes = 0
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        user_agent = user_agent or (
            f"eudamed-toolkit/{__version__} (+https://github.com/Widaeus/eudamed-toolkit)"
        )
        if contact:
            user_agent = f"{user_agent}; contact: {contact}"
        self.session.headers.update({"Accept": "application/json", "User-Agent": user_agent})
        self._gate = threading.Lock()
        self._next_allowed = 0.0

    # ---------------------------------------------------------------- internals

    def _throttle(self) -> None:
        """Hold the gate for the inter-request interval, plus any active cooldown.

        The gate is held across the sleep so that a cooldown triggered by one
        thread stalls every thread. Releasing it and sleeping outside would let
        the other workers keep hammering a server that has just said stop.
        """
        with self._gate:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval

    def _back_off(self, seconds: float) -> None:
        """Pause every thread and permanently slow the client down.

        A 429 means the chosen rate was wrong, not that this one request was
        unlucky. Retrying the failed request at the same rate just earns another
        429, so the interval itself is widened for the rest of the run.
        """
        with self._gate:
            self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)
            self._successes = 0
            if self.min_interval < self.max_interval:
                self.min_interval = min(self.min_interval * 1.5, self.max_interval)
                log.warning(
                    "rate limited; inter-request interval widened to %.2f s",
                    self.min_interval,
                )

    def _note_success(self) -> None:
        """Ease the interval back down once the service is clearly healthy again.

        Without this the interval only ever ratchets up: a brief throttle early
        in a run leaves the client crawling for hours after the service has
        recovered. Recovery is deliberately slower than back-off -- widen fast,
        narrow gently -- and never goes below the configured floor.
        """
        if self.min_interval <= self._floor_interval:
            return
        with self._gate:
            self._successes += 1
            if self._successes >= self.recovery_after:
                self._successes = 0
                self.min_interval = max(self._floor_interval, self.min_interval / 1.25)
                log.info("service healthy; interval eased to %.2f s", self.min_interval)

    @staticmethod
    def _cache_key(path: str, params: dict[str, Any]) -> str:
        blob = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(
        self, path: str, params: dict[str, Any] | None = None, use_cache: bool = True
    ) -> dict[str, Any] | None:
        """GET a JSON endpoint. Returns ``None`` when the record is unavailable.

        EUDAMED answers "no such record" with a 302 to its page-not-found route
        rather than a 404, so a redirect to HTML is treated as a miss.
        """
        params = dict(params or {})
        key = self._cache_key(path, params)
        cache_file = (
            self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None
        )

        if use_cache and cache_file is not None and cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cache_file.unlink(missing_ok=True)

        url = f"{BASE}/{path.lstrip('/')}"
        last_error: str | None = None

        for attempt in range(self.max_retries):
            self._throttle()
            started = time.time()
            try:
                resp = self.session.get(
                    url, params=params, timeout=self.timeout, allow_redirects=False
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(min(2**attempt, 30))
                continue

            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "url": url,
                "params": params,
                "status": resp.status_code,
                "bytes": len(resp.content),
                "elapsed_s": round(time.time() - started, 3),
                "attempt": attempt,
            }

            if resp.status_code in (301, 302, 303, 307, 308):
                # EUDAMED's way of saying "not found".
                record["outcome"] = "not_found_redirect"
                self.run_log.write(record)
                return None

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    record["outcome"] = "non_json_200"
                    self.run_log.write(record)
                    return None
                record["outcome"] = "ok"
                self.run_log.write(record)
                self._note_success()
                if cache_file is not None:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )
                return data

            record["outcome"] = "rate_limited" if resp.status_code == 429 else "http_error"
            self.run_log.write(record)
            last_error = f"HTTP {resp.status_code}"

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    pause = float(retry_after) if retry_after else 0.0
                except ValueError:
                    pause = 0.0
                self._back_off(max(pause, min(2**attempt * 5, 120)))
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(min(2**attempt * 2, 60))
                continue
            return None

        log.warning("giving up on %s %s (%s)", url, params, last_error)
        return None

    # ------------------------------------------------------------------- search

    def search_devices(
        self, page: int = 0, page_size: int = 300, **filters: Any
    ) -> dict[str, Any] | None:
        """One page of the UDI-DI search endpoint."""
        unknown = set(filters) - VERIFIED_DEVICE_FILTERS
        if unknown:
            raise ValueError(
                f"unverified filter(s) {sorted(unknown)}; the API silently ignores "
                "unknown parameters, which would inflate the denominator"
            )
        params = {
            "page": page,
            "pageSize": page_size,
            "size": page_size,
            "iso2Code": "en",
            "languageIso2Code": "en",
            **{k: v for k, v in filters.items() if v is not None},
        }
        # Search pages are volatile (the register changes daily); never cache
        # them -- a caller that wants a durable snapshot should persist them
        # itself.
        return self.get("devices/udiDiData", params, use_cache=False)

    def count_devices(self, **filters: Any) -> int:
        page = self.search_devices(page=0, page_size=1, **filters)
        return int(page["totalElements"]) if page else 0

    def iter_devices(
        self, page_size: int = 300, max_pages: int | None = None, **filters: Any
    ) -> Iterator[dict[str, Any]]:
        """Yield UDI-DI records across all pages of a filtered search."""
        page_no = 0
        while True:
            page = self.search_devices(page=page_no, page_size=page_size, **filters)
            if not page or not page.get("content"):
                return
            yield from page["content"]
            if page.get("last") or (max_pages and page_no + 1 >= max_pages):
                return
            page_no += 1

    # ------------------------------------------------------------------ details

    def device_detail(self, uuid: str) -> dict[str, Any] | None:
        """UDI-DI level record: EMDN codes, market countries, UDI-PI type."""
        return self.get(f"devices/udiDiData/{uuid}", {"languageIso2Code": "en"})

    def basic_udi_detail(self, udi_di_uuid: str) -> dict[str, Any] | None:
        """Basic UDI-DI record reached via a UDI-DI uuid.

        This is where ``deviceName``, ``deviceCriterion`` (LEGACY vs STANDARD)
        and the certificate/notified-body list live -- none of which appear in
        the search response.
        """
        return self.get(
            f"devices/basicUdiData/udiDiData/{udi_di_uuid}", {"languageIso2Code": "en"}
        )

    def actor(self, uuid: str) -> dict[str, Any] | None:
        return self.get(f"actors/{uuid}/publicInformation", {"languageIso2Code": "en"})

    def nomenclature_children(self, cnd_uuid: str) -> dict[str, Any] | None:
        return self.get(
            f"devices/nomenclatures/{cnd_uuid}/children", {"languageIso2Code": "en"}
        )
