"""Client for the DG SANTE Data Lake — the Commission's public bulk CSV endpoint.

    https://api.datalake.sante.service.ec.europa.eu/eudamed

Reached via the Swagger file linked off the EUDAMED Information Centre's
technical documentation page. No API key is required; ``api-version=v1.0`` is
mandatory. It is the same underlying data as the SPA read API, but it returns
**60 columns in one request**, including three that the SPA search endpoint
always returns as null: ``DEVICE_NAME``, ``NOMENCLATURE_CODE`` and
``DEVICE_CRITERION``. That collapses the per-device enrichment cost that a
client built only on the search endpoint would otherwise pay.

Two properties govern how it must be used, both established empirically:

**A hard 1,000-row cap with no pagination.** ``$top``, ``limit``, ``offset`` and
``page`` all 400. Any query returning exactly 1,000 rows is *presumed truncated*
and must be partitioned further. `fetch` flags this rather than silently
returning a short answer, because a truncated pull that looks complete is the
worst failure mode here.

**Only four columns actually filter.** Verified by observing the row count:

===========================  ========  ===================================
Parameter                    Filters?  Note
===========================  ========  ===================================
``SPECIAL_DEVICE_TYPE_ID``   yes       -43 MDR software, -47 IVDR software
``MF_SRN``                   yes       the practical partition key
``BASIC_UDI``                yes       exact, one device
``PRIMARY_DI``               yes       exact, one UDI-DI
``RISK_CLASS_ID``            no        returns empty
``DEVICE_CRITERION``         no        returns empty
``NOMENCLATURE_CODE``        no        returns empty
``LATEST_VERSION``           no        returns empty
===========================  ========  ===================================

A workable pattern is to discover manufacturer SRNs elsewhere — the public
search API's response carries ``manufacturerSrn`` for free — and then pull each
manufacturer's complete device list from the Data Lake by ``MF_SRN``, one
request per manufacturer rather than one per device.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from eudamed import __version__

BASE = "https://api.datalake.sante.service.ec.europa.eu/eudamed"
API_VERSION = "v1.0"
ROW_CAP = 1000

SPECIAL_DEVICE_TYPE = {"mdr_software": -43, "ivdr_software": -47}

VERIFIED_FILTERS = {"SPECIAL_DEVICE_TYPE_ID", "MF_SRN", "BASIC_UDI", "PRIMARY_DI"}
INERT_FILTERS = {
    "RISK_CLASS_ID", "DEVICE_CRITERION", "NOMENCLATURE_CODE", "LATEST_VERSION",
    "MF_COUNTRY_ISO2_CODE", "DEVICE_STATUS_TYPE_ID",
}

log = logging.getLogger("eudamed.datalake")


@dataclass
class Result:
    rows: list[dict[str, Any]]
    truncated: bool
    params: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)


class DataLakeClient:
    def __init__(
        self,
        run_log: Path | str = "logs/datalake_requests.jsonl",
        min_interval: float = 0.5,
        timeout: int = 120,
        max_retries: int = 5,
        user_agent: str | None = None,
        contact: str | None = None,
    ) -> None:
        user_agent = user_agent or (
            f"eudamed-toolkit/{__version__} (+https://github.com/Widaeus/eudamed-toolkit)"
        )
        if contact:
            user_agent = f"{user_agent}; contact: {contact}"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.run_log = Path(run_log)
        self.run_log.parent.mkdir(parents=True, exist_ok=True)
        self._gate = threading.Lock()
        self._next_allowed = 0.0

    def _throttle(self) -> None:
        with self._gate:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = time.monotonic() + self.min_interval

    def _get_csv(self, params: dict[str, Any]) -> str:
        """Issue one throttled, retried, logged GET and return the response body.

        Isolated from `fetch` so tests can replace it without a network call.
        Returns an empty string on any outcome that should read as "no rows" —
        a non-retryable HTTP error, an empty body, or exhausted retries — so
        `fetch` has a single place to interpret an empty result.
        """
        endpoint = params.get("_endpoint", "udi")
        query = {k: v for k, v in params.items() if k != "_endpoint"}
        url = f"{BASE}/{str(endpoint).lstrip('/')}"

        for attempt in range(self.max_retries):
            self._throttle()
            started = time.time()
            try:
                resp = self.session.get(url, params=query, timeout=self.timeout)
            except requests.RequestException as exc:
                log.warning("%s: %s (attempt %d)", type(exc).__name__, exc, attempt)
                time.sleep(min(2**attempt, 30))
                continue

            with self.run_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "url": url,
                    "params": query, "status": resp.status_code,
                    "bytes": len(resp.content),
                    "elapsed_s": round(time.time() - started, 3),
                }) + "\n")

            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(min(2**attempt * 3, 60))
                continue
            if resp.status_code != 200:
                log.warning("HTTP %s for %s", resp.status_code, query)
                return ""
            return resp.text

        log.error("gave up on %s", query)
        return ""

    def fetch(self, endpoint: str = "udi", **filters: Any) -> Result:
        """One Data Lake query. Raises on a filter known to be inert or unverified.

        An inert filter returns an empty body rather than an error, which would
        read as "no such devices" instead of "that parameter does nothing". An
        unverified filter is refused for the same reason: an empty result and a
        parameter that was never checked are indistinguishable from here.
        """
        bad = {k for k in filters if k in INERT_FILTERS}
        if bad:
            raise ValueError(
                f"{sorted(bad)} do not filter this endpoint — they return an empty "
                "body, which would read as 'no devices found'. Partition on "
                f"{sorted(VERIFIED_FILTERS)} instead."
            )
        unknown = {k for k in filters if k not in VERIFIED_FILTERS}
        if unknown:
            raise ValueError(
                f"unverified filter(s) {sorted(unknown)} — only "
                f"{sorted(VERIFIED_FILTERS)} are known to filter this endpoint."
            )

        params = {"api-version": API_VERSION, "format": "csv",
                  **{k: v for k, v in filters.items() if v is not None}}
        text = self._get_csv({"_endpoint": endpoint, **params})
        if not text.strip():
            return Result([], False, params)
        rows = list(csv.DictReader(io.StringIO(text)))
        truncated = len(rows) >= ROW_CAP
        if truncated:
            log.warning("query hit the %d-row cap and is truncated: %s",
                        ROW_CAP, {k: v for k, v in params.items() if k in VERIFIED_FILTERS})
        return Result(rows, truncated, params)

    # ------------------------------------------------------------------ helpers

    def software_frame(self, kind: str = "mdr_software") -> Result:
        """The software-flagged slice. Truncated at 1,000 rows — use for the
        *flag census count*, not for enumeration; enumerate via `by_manufacturer`.
        """
        return self.fetch("udi", SPECIAL_DEVICE_TYPE_ID=SPECIAL_DEVICE_TYPE[kind])

    def by_manufacturer(self, srn: str) -> Result:
        """Every UDI-DI record for one manufacturer, with device names.

        This is the workhorse. Very few manufacturers exceed 1,000 UDI-DIs; those
        that do are reported as truncated so they can be completed from the
        public API.
        """
        return self.fetch("udi", MF_SRN=srn)

    def by_basic_udi(self, basic_udi: str) -> Result:
        return self.fetch("udi", BASIC_UDI=basic_udi)

    def harvest(self, srns: Iterable[str], out_path: Path, workers: int = 4) -> dict[str, Any]:
        """Pull every manufacturer's records to JSONL. Resumable by SRN."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        done: set[str] = set()
        if out_path.exists():
            with out_path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        done.add(json.loads(line)["_query_srn"])
                    except (json.JSONDecodeError, KeyError):
                        continue

        todo = [s for s in dict.fromkeys(srns) if s and s not in done]
        log.info("data lake harvest: %d manufacturers to pull, %d cached",
                 len(todo), len(done))
        written, truncated = 0, []
        with out_path.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(self.by_manufacturer, s): s for s in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                srn = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("harvest failed for %s: %s", srn, exc)
                    continue
                if res.truncated:
                    truncated.append(srn)
                for row in res.rows:
                    row["_query_srn"] = srn
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    written += 1
                if i % 100 == 0:
                    fh.flush()
                    log.info("  %d/%d manufacturers, %d rows", i, len(todo), written)
        if truncated:
            log.warning("%d manufacturers hit the row cap and are INCOMPLETE: %s",
                        len(truncated), truncated[:10])
        return {"manufacturers_pulled": len(todo), "rows_written": written,
                "truncated_srns": truncated}
