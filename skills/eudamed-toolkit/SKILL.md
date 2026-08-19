---
name: using-eudamed-toolkit
description: Use when querying, counting, exporting or analysing EUDAMED (the EU medical device database) with the eudamed-toolkit Python package or the `eudamed` CLI — device searches, Basic UDI-DI and UDI-DI records, EMDN codes, manufacturers and SRNs, the DG SANTE Data Lake bulk CSV, or when a EUDAMED number must be defensible in a report or paper.
---

# Using eudamed-toolkit

## Overview

EUDAMED's public API returns wrong numbers without errors: unknown filters
are silently ignored, `name=` searches the manufacturer, a bulk response of
exactly 1,000 rows is truncated, and the register changes daily. The package
turns those into raised exceptions and refused parameters; the remaining
mistakes are choices only the caller can make — which source, which unit,
which value strings, and how the number is reported. This skill is about
those choices. Full detail: `docs/api-reference.md`,
`docs/datalake-reference.md` in the package repository.

## Which source

| Need | Use | Why |
|---|---|---|
| A count, or a filtered set by EMDN prefix, risk class, legislation, status, device type, pathway | `EudamedClient.count_devices` / `iter_devices` / `eudamed search` | Only the read API filters this way |
| Device name, EMDN code, legacy/MDR pathway for **many** devices | `DataLakeClient.by_manufacturer(srn)` | One request per manufacturer returns them all; the read API nulls them in search results |
| Same fields for **one** device, or the certificate list, manufacturer country, contact | `EudamedClient.basic_udi_detail(udi_di_uuid)` | One request per device |
| Devices registered since yesterday | read API | The Data Lake lags the live register; nothing in it says by how much |
| A reproducible extract | `export_devices(...)` / `eudamed export` | Streams to disk with a manifest; `--resume` continues a dead crawl |
| Decode `-204`-style ids | `ReferenceMaps.load()` / `eudamed reference` | Ids are negative integers; never guess them |

## Value strings that are verified — use these, invent nothing

Read API filters (`VERIFIED_DEVICE_FILTERS`; anything else raises):

- `riskClassCode=refdata.risk-class.` + `class-i | class-iia | class-iib |
  class-iii | class-a | class-b | class-c | class-d`. There is no `class-is`,
  `class-im`, `class-ir`.
- `applicableLegislation=refdata.applicable-legislation.` + `mdr | ivdr | mdd
  | aimdd | ivdd`.
- `deviceStatusCode=refdata.device-model-status.` + `on-the-market |
  no-longer-on-the-market | not-intended-for-eu-market`.
- `deviceTypes=refdata.special-{mdr,ivd,mdd,ivdd,aimdd}-device-type.software`
  — note `ivd`, not `ivdr`; legacy devices carry the flag under their own
  directive, so query all the codes you mean.
- `deviceCriteria=STANDARD | LEGACY | SPP` (MDR/IVDR registration; Art. 120
  transitional; system/procedure pack).
- `cndCode` is a **prefix** match (`Z12` is the whole branch); `tradeName` is
  a diacritic-sensitive substring; `name` matches the manufacturer's name too.

Data Lake `/udi` filters (`VERIFIED_FILTERS`; the rest are refused): `MF_SRN`,
`BASIC_UDI`, `PRIMARY_DI`, `SPECIAL_DEVICE_TYPE_ID` (`-43` MDR, `-47` IVDR,
`-1192` MDD, `-1202` IVDD software), `RISK_CLASS_ID`,
`APPLICABLE_LEGISLATION_ID`, `PLACED_ON_THE_MARKET_ID`, `NOMENCLATURE_CODE`
(exact code, no prefix), `DEVICE_NAME`, `TRADE_NAME`, `REFERENCE`,
`DEVICE_MODEL`, `MEDICAL_PURPOSE`. A row's `NOMENCLATURE_CODE` may hold
several codes, comma-separated, each with a leading space — strip and split.
`DEVICE_CRITERION` on a row is `STANDARD`,
`LEGACY` or `SPP`; `LEGACY` means "under a directive", not "under the MDD" —
`APPLICABLE_LEGISLATION_ID` says which.

## Rules the caller has to keep

1. **Say which unit you counted.** A Basic UDI-DI is a device model; the
   UDI-DIs under it are packaging variants. `count_devices` counts UDI-DIs.
   To count devices, collect distinct `basicUdi` values.
2. **A number without its date is not a result.** The register grows daily.
   Report "N as of <date of pull>", name the filters and the unit, and keep
   the manifest (`export_devices` writes one; `provenance.write_file_manifest`
   for anything else). A resumed export is stitched from two moments; the
   manifest says so — repeat that.
3. **A `truncated` Data Lake result is incomplete**, and looks complete.
   Split it on `RISK_CLASS_ID` or `APPLICABLE_LEGISLATION_ID`, or complete
   the manufacturer from the read API. Never write it out unmarked. And any
   Data Lake pull misses registrations since the export's last refresh —
   say so wherever the result is described.
4. **A raised `RequestFailed` is not zero.** Let it propagate or record it as
   "could not be determined"; do not catch it into an empty result.
5. **Strip contact fields before sharing an extract**
   (`provenance.strip_personal_data`); Basic UDI-DI records carry the
   manufacturer's e-mail and the Data Lake `/actors` endpoint carries named
   individuals.
6. **Country comes from `manufacturer.countryIso2Code`** on the Basic UDI-DI
   record; the SRN prefix is the fallback, and it mixes `GB`/`UK`, `GR`/`EL`
   and uses `XI` for Northern Ireland.
7. **Pass `contact=`** (`--contact`) so the run is identifiable in the
   Commission's logs, and leave the rate limiter alone.

## Pattern: devices, not UDI-DIs, with names

```python
from eudamed.client import EudamedClient
from eudamed.datalake import DataLakeClient

api = EudamedClient(contact="you@example.org")
by_model: dict[str, list[dict]] = {}
for rec in api.iter_devices(deviceTypes="refdata.special-mdr-device-type.software",
                            deviceStatusCode="refdata.device-model-status.on-the-market"):
    by_model.setdefault(rec["basicUdi"], []).append(rec)   # unit: Basic UDI-DI

lake = DataLakeClient(contact="you@example.org")
names: dict[str, str] = {}
for srn in {r[0]["manufacturerSrn"] for r in by_model.values()}:
    res = lake.by_manufacturer(srn)                        # one request per manufacturer
    if res.truncated:
        ...                                                # split or complete from api.basic_udi_detail
    names.update({row["BASIC_UDI"]: row["DEVICE_NAME"] for row in res.rows})
```

## Common mistakes

| Mistake | Instead |
|---|---|
| Guessing a refdata string (`class-is`, `special-ivdr-`) | Use the lists above; an unknown value 400s or returns 0 depending on the parameter |
| Reporting `count_devices()` as "devices" | It is UDI-DIs; group by `basicUdi` |
| ASCII-folding a `tradeName` query | Send the diacritics; `kunstliche` ≠ `künstliche` |
| Treating `name=` hits as device names | It is a recall net over a field that includes the manufacturer name |
| Reading a 1,000-row Data Lake result as complete | Check `Result.truncated` |
| Assuming the Data Lake has today's registrations | It lags; use the read API for recency |
| Mapping `LEGACY` to "MDD" | Could be MDD, AIMDD or IVDD; read the legislation field |
| Quoting a count with no date, filters or unit | Date, filters, unit, manifest — every time |
| Sending `NOMENCLATURE_CODE` as a prefix | Exact codes only; the client adds the stored leading space |
| Trusting a cached detail record's freshness | The cache never expires; `--no-cache` for a fresh pull |
