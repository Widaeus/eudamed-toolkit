# EUDAMED public API — what actually works

The Commission publishes no specification for the read API behind the public
EUDAMED interface. Everything below was established empirically against the
live service and verified on **2026-07-29**, except where a later date is
given.

The one public write-up is OpenRegulatory's unofficial reference —
<https://openregulatory.github.io/eudamed-api/>, source at
`github.com/openregulatory/eudamed-api` (MIT). It is a good starting point for
base URL, path shapes and the page envelope, and the same authors run
[BEUDAMED](https://beudamed.com), a faster search UI over the same data. Checked
against the live site on 2026-07-30 it documents four operations —
`listDevices`, `showDevice`, `showBasicUdiDi`, `showActor` — and is incomplete in
one way that matters here: **it documents none of the query parameters that
actually filter.** No mention of `cndCode`, `riskClassCode`, `deviceStatusCode`
or `applicableLegislation`; `tradeName` and `basicUdi` appear only as response
field names. Working from it alone you would conclude the search endpoint cannot
be filtered at all. It also omits the certificate, notified-body, SSCP,
nomenclature, reference-value and version-history endpoints.

This document is a strict superset of it. The Commission's bulk CSV endpoint
(the DG SANTE Data Lake) is a different service with different behaviour and
has its own reference, [`datalake-reference.md`](datalake-reference.md); the
last section of that file says what the bulk export leaves out.

Base URL: `https://ec.europa.eu/tools/eudamed/api`

The register held **about 2.98 million UDI-DI records on 2026-07-29 and
3,182,822 on 2026-08-19**. Every "whole register" figure below is the one current when
that line was verified.

## How endpoints were discovered

The public interface is an Angular SPA. Its route table and endpoint map sit in
`main.<hash>.js`, retrievable from `https://ec.europa.eu/tools/eudamed/eudamed`,
under the key `environment.api`. Lazy-loaded feature chunks (listed in
`runtime.<hash>.js`) carry the search-form field definitions from which the
candidate filter names were taken; each was then tested against the live API by
observing whether it changed `totalElements`.

**The API silently ignores unknown query parameters.** A misspelled filter
returns the unfiltered result set with a 200 status. This is the single most
dangerous property of the service for research or archival use: a typo does
not fail, it inflates your denominator. Any client built against this API
should refuse a filter name that has not been individually verified this way
rather than pass it through — this package's own client does exactly that; see
`VERIFIED_DEVICE_FILTERS` below.

## Endpoint map

| Purpose | Path |
|---|---|
| Device search (UDI-DI) | `GET /devices/udiDiData` |
| Device detail (UDI-DI) | `GET /devices/udiDiData/{uuid}` |
| Basic UDI-DI via a UDI-DI | `GET /devices/basicUdiData/udiDiData/{uuid}` |
| UDI-DIs under a Basic UDI-DI | `GET /devices/basicUdiData/{uuid}/udiDiData` |
| Device version history | `GET /devices/udiDiData/{ulid}/versions/` |
| EMDN nomenclature tree | `GET /devices/nomenclatures/`, `.../{cndUuid}/children` — see note below |
| Actor (manufacturer, AR, NB) | `GET /actors/{uuid}/publicInformation` |
| Certificates | `GET /certificates/search/`, `GET /certificates/{uuid}` — see below |
| Refused certificates | `GET /applications/search/` — 287 records on 2026-08-19 |
| Notified bodies | `GET /ses/notifiedBodies`, `GET /ses/` — see below |
| SSCP document | `GET /sscp/{ulid}/versions/{versionNumber}` — not exercised |
| Reference value lists | `GET /referenceValues?typeCode={code}` — returned `[]` for every `typeCode` tried; the accepted values are unknown |

The device, Basic UDI-DI and actor **detail** endpoints 400 without
`languageIso2Code`; the certificate and notified-body endpoints answer a bare
GET. Version-history and SSCP endpoints are keyed on the record's **ulid**,
device and actor endpoints on its **uuid**; the search response and the Data
Lake export carry both.

`GET /devices/basicUdiData` (Basic UDI-DI *search*) returns a 302 for every
parameter combination tried; it appears to require state the SPA sets elsewhere.
Basic UDI-DI records are therefore reached only via a UDI-DI uuid: page the
UDI-DI search first, then look up each result's Basic UDI-DI detail if you need
the fields that live only there (see below).

`GET /devices/nomenclatures/` returned **HTTP 500** for every form tried on
2026-08-11: bare, with the full page parameter set (`page`, `pageSize`, `size`,
`iso2Code`, `languageIso2Code`), with `code=Z`, and at
`/devices/nomenclatures/roots`. The row is retained because the path is real —
it appears in the API endpoint map extracted from `main.<hash>.js` — but it
does not currently work, and a reference that listed it as working would be
wrong. Nothing in this package's nomenclature helpers depends on this endpoint
for EMDN *term text*; a caller needing label text for a code will need another
source.

The `/referenceValues` row documents the public API's own endpoint, but this
package does not call it: `eudamed.reference` reads reference values from the
Data Lake instead, at `GET
https://api.datalake.sante.service.ec.europa.eu/eudamed/reference?CODE={code}`
(`api-version=v1.0`, `format=csv`), which returns every language in one CSV
response and is documented nowhere.

`GET /actors/{uuid}/publicInformation` returns the actor record nested under
`actorDataPublicView`, not at the top level — the record's uuid is at
`actorDataPublicView.uuid`. This differs from the device endpoint's flat response
shape. The response also carries a sibling key `importers`. The record has a
`website` field, populated for roughly four actors in five, but it is free
text — literal `n/a`, `tbd`, `-`, two domains in one value, trailing spaces —
so it is not a URL until validated.

**SRNs** are `<ISO2>-<actor type>-<serial>` (`DE-MF-000005430`,
`FR-AR-000000687`); the prefix is the country of the actor's registered place
of business, so a non-EU manufacturer keeps its own prefix and its EU
authorised representative appears separately. The prefixes mix conventions:
`GB-` and `UK-` both occur, as do `GR-` and `EL-`, and `XI-` is Northern
Ireland — normalise before counting by country. `manufacturer.countryIso2Code`
on the Basic UDI-DI record is a second, independent country signal and the
two can disagree. Notified bodies are identified by their four-digit NB number
(`0633`), not an SRN, throughout the certificate records. There is no
headquarters or ownership field anywhere in the public schema.

## Interface routes

The REST paths above are not addresses a person can open. The public interface
is an Angular single-page application, and its route table sits in
`main.<hash>.js` with the lazy feature chunks listed in `runtime.<hash>.js`.
Read on 2026-08-11 from `search-device_search-device_module_ts` and
`search-eo_search-eo_module_ts`, both of which declare exactly two routes, `''`
and `':uuid'`:

| Screen | Route | Key |
|---|---|---|
| Device | `#/screen/search-device/{uuid}` | UDI-DI uuid |
| Economic operator | `#/screen/search-eo/{uuid}` | actor uuid |

Both take uuids. Neither accepts a Basic UDI-DI code, a primary DI or an SRN,
and the search screen calls `router.navigate([...], {queryParams: {}})` on load,
so `#/screen/search-device?basicUdi=<code>` opens an empty search form rather
than the device.

The actor uuid appears in no search response. It is read from
`manufacturer.uuid` on the Basic UDI-DI detail record, so it costs one request
per manufacturer.

## `GET /devices/udiDiData`

Always-required (the endpoint 400s without them):
`page`, `pageSize`, `size`, `iso2Code`, `languageIso2Code`.
`pageSize` is capped at 300.

### Filters verified to work

| Parameter | Semantics | Note |
|---|---|---|
| `cndCode` | EMDN code, **prefix match** | `Z12` → 206,769 UDI-DIs; `Z1203` → 41,824 (2026-07-29), 44,455 (2026-08-11) |
| `riskClassCode` | full refdata code | e.g. `refdata.risk-class.class-iib` |
| `deviceStatusCode` | full refdata code | e.g. `refdata.device-model-status.on-the-market` |
| `applicableLegislation` | full refdata code | e.g. `refdata.applicable-legislation.mdr` |
| `tradeName` | substring, case-insensitive | |
| `name` | substring over a concatenated name field | see caveat below |
| `primaryDi` | exact | |
| `basicUdi` | exact | |
| `deviceTypes` | full refdata code | `refdata.special-mdr-device-type.software` → 4,726 UDI-DIs (2026-07-30), 4,915 (2026-08-19); `refdata.special-ivd-device-type.software` → 687 (2026-07-30), 701 (2026-08-19). **Legacy devices carry the flag under their own directive's code**: `refdata.special-mdd-device-type.software` → 917, `refdata.special-ivdd-device-type.software` → 204, `refdata.special-aimdd-device-type.software` → 10 (2026-08-19). Note the IVDR value is spelled `special-ivd-`; `special-ivdr-` returns 0. |
| `deviceCriteria` | exact | `STANDARD` (MDR/IVDR) → 2,279,179 (2026-07-30), 2,410,653 (2026-08-19); `LEGACY` (Art. 120 transitional) → 651,311, 708,697; **`SPP`** (system/procedure pack, Art. 22) → 63,472 (2026-08-19). The three sum to the register. |

The `cndCode=Z1203` figures above are not a typo repeated twice: they are the
same query, three weeks apart. The register is a live, growing dataset, not a
static file — any count taken from it is a snapshot as of the date it was
pulled, and a figure quoted without that date is already ambiguous.

#### The refdata vocabularies, with counts

Every value below was verified as a filter on 2026-08-19; the count is
`totalElements` that day, out of 3,182,822.

| `riskClassCode` = `refdata.risk-class.` | UDI-DIs |
|---|---|
| `class-i` | 1,524,173 |
| `class-iia` | 854,578 |
| `class-iib` | 507,319 |
| `class-iii` | 146,952 |
| `class-a` / `class-b` / `class-c` / `class-d` (IVDR) | 57,904 / 14,743 / 9,591 / 165 |

`class-is`, `class-im` and `class-ir` are **not** values — the request 400s.
The register does not separate sterile, measuring or reusable-surgical Class I
from Class I. The eight values above sum to 3,115,425, so about 67,000
UDI-DIs — the legacy IVD classes (Annex II lists, general, self-testing) and
AIMDD — have a risk class this filter cannot name.

| `applicableLegislation` = `refdata.applicable-legislation.` | UDI-DIs |
|---|---|
| `mdr` | 2,391,722 |
| `ivdr` | 82,403 |
| `mdd` | 641,300 |
| `aimdd` | 523 |
| `ivdd` | 66,874 |

These five sum exactly to the register.

| `deviceStatusCode` = `refdata.device-model-status.` | UDI-DIs |
|---|---|
| `on-the-market` | 3,113,569 |
| `no-longer-on-the-market` | 41,449 |
| `not-intended-for-eu-market` | 27,804 |

These three sum exactly to the register; `no-longer-placed-on-the-market`
and `recalled` are not values and 400.

**An unknown value is not always ignored.** `riskClassCode` and
`deviceStatusCode` answer an unrecognised value with HTTP 400; `deviceTypes`
answers one with `totalElements: 0`. Neither is the silent full-register
answer an unknown parameter *name* gets, but a 0 from `deviceTypes` still
needs the value checked against the list above before it is believed.

#### `tradeName` and `cndCode` — two more ways to be wrong quietly

`tradeName` is a raw substring match with no word boundary and **diacritic
sensitivity**: `künstliche` matched 9 records and `kunstliche` 0, so folding a
query to ASCII silently destroys recall; and a short generic term matches in
bulk — the token `AI` is a substring of 122,077 trade names, almost all
`PAIN`, `AIR` and `MAIN`, and `Jazz`, `Iris`, `HALO` and `Rapid` each pull
thousands of unrelated devices. In one exercise 92% of the records a list of
product names retrieved were noise. Treat a trade-name hit as a candidate.

`cndCode` is a prefix match, and at a non-terminal code the prefix is the
whole branch: `Z` alone matches every device in category Z (147,900 UDI-DIs
when tried), and `Z12` matches 206,769. Query terminal codes unless you mean
the branch.

### Filters verified NOT to work

Silently ignored (returns the whole register): `deviceName`, `manufacturerName`,
`manufacturerSrn`, `manufacturerCountryIso2Code`, `countryIso2Code`,
`legislationCode`, `versionStatusCode`, `udiDi`, `searchText`, `text`,
`keyword`, `emdnCode`, `nomenclatureCode`, `riskClass`, `sortField`,
`sortDirection`.

There is **no server-side sort**, and no server-side manufacturer-country
filter. Country of manufacture must be derived client-side from the SRN prefix
or from the enriched manufacturer record.

### `name` — read the caveat

`name` matches a concatenation that includes the manufacturer's registered
name, so `name=Siemens` returns devices from *Varian Medical Systems* (a
Siemens Healthineers company). It is a useful recall net but is **not** a
device-name search, and a hit does not mean the device is named that. Any
count derived from it needs the manufacturer-name confound stated.

### Response shape

Spring Data page envelope: `content[]`, `totalElements`, `totalPages`, `first`,
`last`, `number`, `size`. Errors are non-standard — a missing record returns
**302** to an internal `page-not-found` route, never 404, so redirects must be
treated as misses and `allow_redirects=False` set on the client.

Pages come back in register (insertion) order, newest first: on the morning
of 2026-08-19, page 0 held records whose `versionDate` was that same morning.
Two consequences. A truncated crawl is an ordered subset — the newest slice —
not a sample. And because there is no server-side sort, one registration
while a crawl is paused shifts every later record by one position.

## What the search response does and does not contain

Present: `basicUdi`, `primaryDi`, `uuid`, `riskClass`, `tradeName`,
`manufacturerName`, `manufacturerSrn`, `deviceStatusType`,
`authorisedRepresentativeName`, `reference`.

Always `null` in search results even though the field exists:
`deviceName`, `deviceModel`, `lastUpdateDate`, `applicableLegislation`,
`issuingAgency`, `deviceCriterion`, `sterile`, `multiComponent`.

Also present and useful: `latestVersion` (bool) and `versionNumber` (int).
A Basic UDI-DI carries about 2.5 UDI-DIs, so a link to "the" device needs a
deterministic pick — `latestVersion == true`, then highest `versionNumber`,
then lexically first uuid is one that is stable across runs.

The device name therefore costs one extra request per device. Timestamps do
exist, but only on the detail records: `GET /devices/udiDiData/{uuid}` and the
Basic UDI-DI record both carry `versionDate` and `lastUpdated`
(`2026-08-19T05:01:37.812`), and the nested `manufacturer` and
`authorisedRepresentative` blocks carry their own `lastUpdateDate`. Nothing
on the search endpoint filters on them.

## `GET /devices/basicUdiData/udiDiData/{uuid}` — the important one

This is where the fields most likely to matter for a downstream analysis live:

- `deviceName` — the manufacturer's device name (the only device-level free
  text this API returns)
- `deviceCriterion` — `LEGACY` (Art. 120 MDD/AIMDD/IVDD transitional),
  `STANDARD` (MDR/IVDR) or `SPP` (system/procedure pack). This is populated on
  the Basic UDI-DI record; it does not appear in the search response. It can
  disagree with `legislation.code` on the same record; report the disagreement
  rather than resolving it, it is a data-quality finding in its own right.
- `deviceCertificateInfoList[]` — certificate number, expiry, and the full
  notified-body actor record including country. **Populated mainly for legacy
  devices** of Class IIa and above, where the manufacturer declares the
  MDD/AIMDD certificate at registration; essentially empty for MDR-registered
  devices, whose certificates enter through the certificates module (below)
  and link back through that module's `scopes`.
- `manufacturer` — SRN, `countryIso2Code`, address, and **contact email**.
  This block is itself a versioned actor record (`uuid`, `versionNumber`,
  `latestVersion`); its `uuid` is what the interface's economic-operator page
  takes.
- `medicalPurpose` — free text, populated on roughly 2% of records; see the
  next section.
- `legislation`, `riskClass`, `implantable`, `active`, `measuringFunction`,
  `specialDeviceType`, `linkedSscp`, `clinicalInvestigationLinks`

`manufacturer.electronicMail` is personal data under GDPR when it identifies a
natural person. Strip it before depositing or redistributing any extract built
from this endpoint.

## No intended-purpose field worth screening on

**EUDAMED's public device schema contains no usable intended-purpose text.**
There is no `intendedPurpose` field on the UDI-DI record or the Basic UDI-DI
record. What exists is thinner than the name suggests:

- `medicalPurpose` on the Basic UDI-DI record (`MEDICAL_PURPOSE` in the Data
  Lake export) is free text populated on about 2% of records, with values from
  a paragraph of prose to the word `No`. It is not searchable through the
  search endpoint.
- Certificate detail records carry an `intendedPurpose` per entry of
  `scopes[]`. In a 642-certificate sample read on 2026-08-18, 205 of 2,120
  scope entries (9.7%) had one, and only 583 (27.5%) linked to a Basic UDI-DI
  at all — the module is dominated by quality-management-system certificates,
  which describe no device.
- SSCP documents contain intended-purpose prose, for Class III and
  implantable devices only.

Consequently, any text matching built on the search endpoint sees nothing but
the device name, trade name and manufacturer name — and the manufacturer name
only as part of the concatenation `name` matches. Anything that depends on a
device's stated purpose has to come from outside the public API, or from the
sparse fields above with their coverage stated.

## Certificates and notified bodies

Verified 2026-08-19.

`GET /certificates/search/` — the same page envelope as the device search;
**4,443 certificates** that day. A bare GET works and defaults to `size: 20`;
`pageSize=300` is honoured (there is no cap at 20). Filters verified to change
`totalElements`: `actorSrn` (manufacturer SRN), `notifiedBodySrn` (four-digit
NB number, `0633` → 16), `certificateNumber` (exact). An unknown parameter is
silently ignored, exactly as on the device search. Search-record fields:
`ulid, uuid, notifiedBodySrn, actorSrn, actorName, actorNames, mfStatus,
prStatus, arStatuses, certificateNumber, certificateType, issueDate,
expiryDate, startingValidityDate, certificateStatus, versionNumber,
authorizedRepresentativeSrns, latestVersion, revisionNumber, versionState`.
`certificateType.code` values seen: `refdata.certificate-mdr-type.
quality-management-system | technical-documentation | quality-assurance` and
the `certificate-ivdr-type` equivalents; `certificateStatus.code`:
`refdata.certificate-status.issued | supplemented | amended | reissued |
restricted | cancelled`.

`GET /certificates/{uuid}` — 60-odd keys, including `certificateId`
(NB number + certificate number), `applicableLegislation`, `type`, `status`,
the full nested `manufacturer`, `authorisedRepresentatives` and
`notifiedBody` actor records, `documents`, `conditions`, `scopes[]`,
`sscps`, `precedingCertificates`, `intendedMedicalPurpose`,
`ivdrMechanismOfScrutiny`, `versionDate`. **`scopes[]` is the device-level
link**: each entry may carry `basicUdiData`, `riskClass`, `deviceTypeCodes`,
`description` and `intendedPurpose`, but most do not (see above).

`GET /applications/search/` — refused certificates, same envelope; 287
records. Fields include `applicationReferenceNumber`,
`conformityAssessmentProcedure`, `certificateRefusalDate`.

`GET /ses/notifiedBodies` — a bare JSON list (53 entries) of
`{uuid, name, eudamedIdentifier, legislationStatusMap}`, where
`eudamedIdentifier` is the four-digit NB number. `GET /ses/` — a page
envelope of 70 notified-body actor records (`actorType.code =
refdata.actor-type.notified-body`, `srnCode: NB`) with country and
registration date.

## EMDN

The authoritative term list is the Commission's annual spreadsheet,
<https://webgate.ec.europa.eu/dyna2/emdn/build/EMDN%20v2026_EN.xlsx> (v2026:
8,516 terms; columns `CATEGORY, CATEGORY DESCRIPTION, CODE, TERM EMDN, LEVEL,
TERMINAL LEVEL`). The EMDN browser at `webgate.ec.europa.eu/dyna2/emdn/` is a
client-side application whose pages are byte-identical for every code, so it
cannot be scraped by URL; and this API's own nomenclature endpoint 500s (above),
so the spreadsheet is the only working source of term text.

Structure: a category letter, a two-digit group, then up to five levels of
type. Terminal codes ending `…92` are `MEDICAL DEVICE SOFTWARE` (all 173 of
them contain "SOFTWARE" in v2026), `…82` are `SOFTWARE ACCESSORIES`, `…80`
hardware accessories, `…85` consumables, `…99` other. `V92` is the catch-all
"medical device software not included in other classes" and is the most used
software code in practice; manufacturers do not always follow the suffix
convention (`Z110603`, PACS, is widely used for software). Z11 and Z12 are
equipment groups — bioimaging/radiotherapy and functional exploration —
not software branches. In the Data Lake export EMDN codes carry a leading
space and multi-code devices store a comma-separated list; strip and split
before joining.

## The Commission's own documentation, for what it covers

There is no official documentation of this read API. What the Commission
publishes describes the *registration* side, and is still the closest thing
to a specification of the fields:

- Technical documentation page:
  <https://webgate.ec.europa.eu/eudamed-help/en/documentation/technical-documentation.html>
  (v2.27.0, 2026-07-24). Its links point at `/en/documentation/<file>`,
  which 404s; the files are under `/en/files/<file>` with spaces URL-encoded.
- `UDI Devices - data dictionary.xlsx` — per-field ids (`FLD-UDID-nnn`),
  enum references, public-access flag and XSD element name for Basic UDI-DI,
  UDI-DI, legacy, SPP and container-pack records.
- `XSD schemas.zip` (v3.0.30) — the machine-readable data model with
  `xs:documentation` cross-referencing those field ids.
- `UDI Devices - enumerations.pdf`, `EUD - enumerations.pdf`,
  `UDI Devices - business rules.pdf`.
- `Swagger OpenAPI file.yaml` — the Data Lake endpoint, not this API.

The special-device-type software flag (`FLD-UDID-13`, `specialDevice` in the
XSD) is a Commission implementation field applicable to MDR, IVDR, MDD, AIMDD
and IVDD registrations; it is not one of MDR Annex VI Part B's core UDI data
elements. All UDI-DIs under a flagged Basic UDI-DI inherit it. The
Commission's manual bulk XML download is available only to a registered
actor for its own data; there is no public file extract, and no EUDAMED
dataset on data.europa.eu.

## Performance, throttling and politeness

Measured 2026-07-29 across a single 3-hour extraction: **5,084 requests, of which
4,688 returned 200, 378 returned 429 (7.4%) and 18 returned 500.**

| Request | Median |
|---|---|
| count query (`pageSize=1`) | ~5 s |
| full page (`pageSize=300`) | ~6–10 s, degrading with page depth |
| `name=` substring query | ~33 s |
| detail / Basic UDI-DI fetch | ~1–2 s |

**The service does rate-limit, and it does so without telling you how.** Sustained
request rates above roughly 4 per second produced HTTP 429 within a minute or two.
**No `Retry-After` header was returned on any of the 378 throttled responses**, so
a client cannot learn the correct backoff from the service and must discover it by
probing. Throttling also persists: a burst earns 429s for minutes afterwards, well
past the point at which the offending requests have stopped.

A client should treat a 429 as evidence that the chosen rate was wrong rather
than that one request was unlucky: widen the inter-request interval for every
worker (not only the one that was refused), and ease back toward the
configured floor only after sustained success. Without that last part, one
early throttle leaves a long run crawling for hours after the service has
recovered. This package's client does exactly this.

Practical rates: about 1 request per second is sustainable for the detail
endpoints; 4 per second is not. On a run of 2026-08-18 the realised interval
against a configured 0.5 s floor was 0.9–1.4 s — the service, not the client,
set the pace. Paging the whole register at 300 per page is roughly 10,600
requests at its 2026-08-19 size; paging a full EMDN branch filtered by
`cndCode` took roughly 690 requests at 300 records per page (measured
2026-07-29). Budget accordingly, and be conservative — this is shared public
infrastructure.
