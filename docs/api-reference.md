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

This document is a strict superset of it.

Base URL: `https://ec.europa.eu/tools/eudamed/api`

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
| Certificates | `GET /certificates/search/`, `GET /certificates/{uuid}` |
| Refused certificates | `GET /applications/search/` |
| Notified bodies | `GET /ses/notifiedBodies`, `GET /ses/` |
| SSCP document | `GET /sscp/{ulid}/versions/{versionNumber}` |
| Reference value lists | `GET /referenceValues?typeCode={code}` |

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
shape. The response also carries a sibling key `importers`.

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
| `deviceTypes` | full refdata code | `refdata.special-mdr-device-type.software` → 4,726 UDI-DIs; `refdata.special-ivd-device-type.software` → 687 UDI-DIs (2026-07-30) |
| `deviceCriteria` | exact | `STANDARD` (MDR) → 2,279,179; `LEGACY` (Art. 120 MDD/AIMDD transitional) → 651,311 (2026-07-30) |

The `cndCode=Z1203` figures above are not a typo repeated twice: they are the
same query, three weeks apart. The register is a live, growing dataset, not a
static file — any count taken from it is a snapshot as of the date it was
pulled, and a figure quoted without that date is already ambiguous.

### Filters verified NOT to work

Silently ignored (returns the full 2.98 M set): `deviceName`, `manufacturerName`,
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

## What the search response does and does not contain

Present: `basicUdi`, `primaryDi`, `uuid`, `riskClass`, `tradeName`,
`manufacturerName`, `manufacturerSrn`, `deviceStatusType`,
`authorisedRepresentativeName`, `reference`.

Always `null` in search results even though the field exists:
`deviceName`, `deviceModel`, `lastUpdateDate`, `applicableLegislation`,
`issuingAgency`, `deviceCriterion`, `sterile`, `multiComponent`.

The device name therefore costs one extra request per device.

## `GET /devices/basicUdiData/udiDiData/{uuid}` — the important one

This is where the fields most likely to matter for a downstream analysis live:

- `deviceName` — the manufacturer's device name (the only device-level free
  text this API returns)
- `deviceCriterion` — `LEGACY` (Art. 120 MDD/AIMDD transitional) vs `STANDARD`
  (MDR). This is populated on the Basic UDI-DI record; it does not appear in
  the search response.
- `deviceCertificateInfoList[]` — certificate number, expiry, and the full
  notified-body actor record including country
- `manufacturer` — SRN, `countryIso2Code`, address, and **contact email**
- `legislation`, `riskClass`, `implantable`, `active`, `measuringFunction`,
  `specialDeviceType`, `linkedSscp`, `clinicalInvestigationLinks`

`manufacturer.electronicMail` is personal data under GDPR when it identifies a
natural person. Strip it before depositing or redistributing any extract built
from this endpoint.

## No intended-purpose field

**EUDAMED's public schema contains no intended-purpose free text.** There is no
`intendedPurpose` field on the UDI-DI record or the Basic UDI-DI record. The
only intended-purpose prose in the public database sits inside SSCP documents,
which exist for Class III and implantable devices only.

Consequently, any text matching built on this API sees nothing but the device
name, trade name and manufacturer name — and the manufacturer name only as
part of the concatenation `name` matches. Anything that depends on a device's
stated purpose has to come from outside the public API.

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
endpoints; 4 per second is not. Paging the whole 2.98 M-record register at 300 per
page is roughly 10,000 requests; paging a full EMDN branch filtered by
`cndCode` took roughly 690 requests at 300 records per page (measured
2026-07-29). Budget accordingly, and be conservative — this is shared public
infrastructure.
