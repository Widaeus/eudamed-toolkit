"""Client for the DG SANTE Data Lake — the Commission's public bulk CSV endpoint.

    https://api.datalake.sante.service.ec.europa.eu/eudamed

Reached via the Swagger file linked off the EUDAMED Information Centre's
technical documentation page. No API key is required; ``api-version=v1.0`` is
mandatory. It is the same underlying data as the SPA read API, but it returns
**60 columns in one request**, including three that the SPA search endpoint
always returns as null: ``DEVICE_NAME``, ``NOMENCLATURE_CODE`` and
``DEVICE_CRITERION``. That collapses the per-device enrichment cost that a
client built only on the search endpoint would otherwise pay. The full
empirical reference is ``docs/datalake-reference.md``.

Two properties govern how it must be used, both established empirically:

**A hard 1,000-row cap with no pagination.** ``$top``, ``limit``, ``offset`` and
``page`` all 400. Any query returning exactly 1,000 rows is *presumed truncated*
and must be partitioned further. `fetch` flags this rather than silently
returning a short answer, because a truncated pull that looks complete is the
worst failure mode here.

**Only some columns are accepted as filters, and the rest are refused with
HTTP 400.** Verified by sending each column name as a parameter:

===========================  ============  =====================================
Parameter                    Match         Note
===========================  ============  =====================================
``MF_SRN``                   exact         the practical partition key
``BASIC_UDI``                exact         one device model
``PRIMARY_DI``               exact         one UDI-DI
``SPECIAL_DEVICE_TYPE_ID``   exact         -43 MDR / -47 IVDR / -1192 MDD /
                                           -1202 IVDD software
``RISK_CLASS_ID``            exact         negative reference id, e.g. -204
``APPLICABLE_LEGISLATION_ID`` exact        e.g. -197 (MDR)
``PLACED_ON_THE_MARKET_ID``  exact         reference id
``NOMENCLATURE_CODE``        exact         stored with a leading space, and
                                           no prefix match -- see `fetch`
``DEVICE_NAME``              exact, ci     whole field, case-insensitive
``TRADE_NAME``               exact, ci     whole field, case-insensitive
``REFERENCE``                exact, ci
``DEVICE_MODEL``             exact
``MEDICAL_PURPOSE``          exact
===========================  ============  =====================================

Every other column -- ``DEVICE_CRITERION``, ``DEVICE_STATUS_TYPE_ID``,
``LATEST_VERSION``, ``STATUS_ID``, ``MF_NAME``, ``UUID``, ``ID``, the ULIDs,
``AR_SRN`` and every boolean flag -- returns HTTP 400 with an empty body, as
does any name that is not a column. Filters combine with AND.

An earlier version of this module listed ``RISK_CLASS_ID`` as inert. It is
not: the reference ids are negative integers (Class IIa is -204), and a
guessed value matches nothing, which reads as "inert" if the check is a row
count.

A workable pattern is to discover manufacturer SRNs elsewhere -- the public
search API's response carries ``manufacturerSrn`` for free -- and then pull each
manufacturer's complete device list from the Data Lake by ``MF_SRN``, one
request per manufacturer rather than one per device. A manufacturer that hits
the cap can be split further on ``RISK_CLASS_ID`` or ``APPLICABLE_LEGISLATION_ID``.
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

from eudamed import user_agent as _default_user_agent
from eudamed.errors import RequestFailed

BASE = "https://api.datalake.sante.service.ec.europa.eu/eudamed"
API_VERSION = "v1.0"
ROW_CAP = 1000

SPECIAL_DEVICE_TYPE = {
    "mdr_software": -43,
    "ivdr_software": -47,
    "mdd_software": -1192,   # legacy (Art. 120) devices under the MDD
    "ivdd_software": -1202,  # legacy devices under the IVDD
}

# Columns the endpoint accepts as query parameters (verified by sending each
# column name and observing the rows returned; the set matches the
# Commission's OpenAPI file for the endpoint).
VERIFIED_FILTERS = frozenset({
    "MF_SRN", "BASIC_UDI", "PRIMARY_DI", "SPECIAL_DEVICE_TYPE_ID",
    "RISK_CLASS_ID", "APPLICABLE_LEGISLATION_ID", "PLACED_ON_THE_MARKET_ID",
    "NOMENCLATURE_CODE", "DEVICE_NAME", "TRADE_NAME", "REFERENCE",
    "DEVICE_MODEL", "MEDICAL_PURPOSE",
})
# Columns of the export that the endpoint refuses as query parameters with
# HTTP 400 and an empty body. Listed so the refusal can be explained locally
# rather than surfacing as a failed request.
REJECTED_FILTERS = frozenset({
    "ID", "UDI_DI_DATA_ULID", "UUID", "LATEST_VERSION", "CMR_SUBSTANCE",
    "ENDOCRINE_DISRUPTOR", "LATEX", "REPROCESSED", "STERILE", "STERILIZATION",
    "NEW_DEVICE", "VERSION_NUMBER", "BASIC_UDI_DATA_UUID", "BASIC_UDI_DATA_ULID",
    "ACTIVE", "ADMINISTERING_MEDICINE", "ANIMAL_TISSUES", "COMPANION_DIAGNOSTICS",
    "HUMAN_TISSUES", "IMPLANTABLE", "KIT", "MEASURING_FUNCTION",
    "MICROBIAL_SUBSTANCES", "NEAR_PATIENT_TESTING", "REUSABLE", "SELF_TESTING",
    "REAGENT", "MULTI_COMPONENT_ID", "INSTRUMENT", "PROFESSIONAL_TESTING",
    "SUTURES", "HUMAN_PRODUCT", "MEDICINAL_PRODUCT", "DEVICE_CRITERION",
    "MF_NAME", "DEVICE_STATUS_TYPE_ID", "MF_ACTOR_NAMES",
    "ACTOR_ABBREVIATED_NAMES", "STATUS_ID", "AR_NAME", "AR_SRN",
    "AR_ACTOR_NAMES", "UNIT_OF_USE_DI", "DIRECT_MARKETING_DI", "SECONDARY_DI",
    "CONTAINER_PACKAGE_DIS", "SUBSTATUSES",
    # not a column at all, but a name people reach for
    "MF_COUNTRY_ISO2_CODE",
})
# Kept for callers of the 0.1.0 name; the semantics were never "inert".
INERT_FILTERS = REJECTED_FILTERS

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
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": _default_user_agent(contact, user_agent)}
        )
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
        Returns the body — including an empty one, which is this endpoint's
        legitimate way of saying "no rows match". Any outcome that means the
        query was *not answered* — a non-retryable HTTP error, a transport
        failure, exhausted retries — raises ``RequestFailed`` instead, because
        an outage returned as an empty body is indistinguishable from a
        manufacturer with no registrations.
        """
        endpoint = params.get("_endpoint", "udi")
        query = {k: v for k, v in params.items() if k != "_endpoint"}
        url = f"{BASE}/{str(endpoint).lstrip('/')}"
        last_status: int | None = None
        last_error: str | None = None
        attempt = 0

        for attempt in range(self.max_retries):
            self._throttle()
            started = time.time()
            try:
                resp = self.session.get(url, params=query, timeout=self.timeout)
            except requests.RequestException as exc:
                log.warning("%s: %s (attempt %d)", type(exc).__name__, exc, attempt)
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(min(2**attempt, 30))
                continue

            with self.run_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "url": url,
                    "params": query, "status": resp.status_code,
                    "bytes": len(resp.content),
                    "elapsed_s": round(time.time() - started, 3),
                }) + "\n")

            last_status = resp.status_code
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(min(2**attempt * 3, 60))
                continue
            if resp.status_code != 200:
                log.warning("HTTP %s for %s", resp.status_code, query)
                raise RequestFailed(
                    url, query, status=resp.status_code, attempts=attempt + 1
                )
            # Served as bare ``text/csv`` with no charset, so ``resp.text``
            # would be decoded as ISO-8859-1 and mangle every accented
            # manufacturer name. The body is UTF-8.
            return resp.content.decode("utf-8")

        log.error("gave up on %s", query)
        raise RequestFailed(
            url, query, status=last_status, attempts=attempt + 1, reason=last_error
        )

    def fetch(self, endpoint: str = "udi", **filters: Any) -> Result:
        """One Data Lake query. Raises on a filter the endpoint refuses or one
        that has not been verified against it.

        A column the endpoint refuses is answered with HTTP 400 and an empty
        body; refusing it here names the column instead of surfacing a failed
        request. An unverified name is refused for the reason the whole
        package exists: an empty result and a parameter that was never checked
        are indistinguishable from here.

        ``NOMENCLATURE_CODE`` is sent in the form the export stores it -- with a
        leading space (``" Z12110102"``) -- because the match is exact and the
        code as a person writes it returns zero rows with HTTP 200.

        A query the service could not answer raises ``RequestFailed``. Only a
        query the service *did* answer, with no matching rows, comes back as an
        empty ``Result``.
        """
        bad = {k for k in filters if k in REJECTED_FILTERS}
        if bad:
            raise ValueError(
                f"{sorted(bad)} are not accepted as filters by this endpoint — it "
                "answers HTTP 400. Partition on "
                f"{sorted(VERIFIED_FILTERS)} instead."
            )
        unknown = {k for k in filters if k not in VERIFIED_FILTERS}
        if unknown:
            raise ValueError(
                f"unverified filter(s) {sorted(unknown)} — only "
                f"{sorted(VERIFIED_FILTERS)} are known to filter this endpoint."
            )
        code = filters.get("NOMENCLATURE_CODE")
        if isinstance(code, str) and code.strip():
            filters["NOMENCLATURE_CODE"] = " " + code.strip()

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
        """The software-flagged slice for one legislation: ``mdr_software``,
        ``ivdr_software``, ``mdd_software`` or ``ivdd_software``. The MDR slice
        exceeds the 1,000-row cap, so use it for presence, not enumeration;
        enumerate by manufacturer via `by_manufacturer`, or split it on
        ``RISK_CLASS_ID``. The other three have fitted under the cap.
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
        """Pull every manufacturer's records to JSONL. Resumable by SRN.

        A manufacturer whose query failed is recorded in ``failed_srns`` and
        counted separately from those actually pulled, because reporting a
        failed pull as a manufacturer with zero devices makes the gap
        invisible. The resume set is taken from the SRNs present in the output
        file, so a failed manufacturer -- like one that genuinely has no rows
        -- is attempted again on the next run.
        """
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
        written, pulled = 0, 0
        truncated: list[str] = []
        failed: list[str] = []
        with out_path.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(self.by_manufacturer, s): s for s in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                srn = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("harvest failed for %s: %s", srn, exc)
                    failed.append(srn)
                    continue
                pulled += 1
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
        if failed:
            log.error("%d of %d manufacturers could not be pulled and are MISSING "
                      "from the output: %s", len(failed), len(todo), failed[:10])
        return {"manufacturers_requested": len(todo),
                "manufacturers_pulled": pulled,
                "manufacturers_failed": len(failed),
                "failed_srns": failed,
                "rows_written": written,
                "truncated_srns": truncated}
