# DG SANTE Data Lake — the EUDAMED bulk endpoint, what actually works

The Commission publishes an OpenAPI file for this endpoint — which names the
endpoints and the query parameters each accepts, and is accurate as far as it
goes — but no prose documentation, no rate limit, no row cap, no match
semantics, no column list and no statement of what the export does and does
not contain. Everything below was established empirically
against the live service, and records behaviour rather than counts: the
register grows daily and any figure pulled from it belongs in the manifest
next to the extract, not here. It complements
[`api-reference.md`](api-reference.md), which covers the JSON read API behind
the public interface; the two expose the same register with different
strengths, and this file ends with what the Data Lake leaves out.

Base URL: `https://api.datalake.sante.service.ec.europa.eu/eudamed`

## Where it comes from

The endpoint is described by an OpenAPI file titled *API - EUDAMED Public*,
linked as "Swagger (OpenAPI) file" under *Miscellaneous* on the EUDAMED
Information Centre's technical documentation page,
<https://webgate.ec.europa.eu/eudamed-help/en/documentation/technical-documentation.html>.
Two traps on that page:

- Its `href`s point at `/en/documentation/<file>`, which 404s. The files are
  at `/en/files/<file>` with the spaces URL-encoded — the Swagger file is
  <https://webgate.ec.europa.eu/eudamed-help/en/files/Swagger%20OpenAPI%20file.yaml>.
- The same page links the UDI data dictionary (`UDI Devices - data
  dictionary.xlsx`), the enumerations PDFs and the XSD schema bundle
  (`XSD schemas.zip`). Those describe the *registration* data model, whose
  field ids (`FLD-UDID-nnn`) are the closest thing to a specification of what
  the columns below mean.

The OpenAPI file declares, per endpoint, the query parameters the service
accepts, and the service agrees with it exactly: every parameter it lists
filters, and every name it does not list is refused. What it leaves out is
everything else in this file — the row cap and the absence of paging, the
`api-version` parameter, the match semantics, the response columns, the
charset, and what the export omits.

The OpenAPI file declares an Azure API Management key scheme
(`Ocp-Apim-Subscription-Key` header / `subscription-key` query). **No key has
been required**; requests without one succeed. The DG SANTE developer portal
(`developer.datalake.sante.service.ec.europa.eu/docs`) says its documentation
is under construction and documents only the mandatory `api-version`
parameter and a deprecation/brownout policy. There is no published rate
limit, quota or terms of use for this endpoint; the SPA's own help pages
describe a public bulk download only as "the link at the top of the
Devices/SPPs search page", without naming this host. Treat the whole thing as
liable to change without notice.

## Requests

Two parameters are always required:

| Parameter | Without it |
|---|---|
| `api-version=v1.0` | HTTP 404 `{"statusCode":404,"message":"Resource not found"}` |
| `format=csv` or `format=json` | HTTP 400 `Required parameter format missing.` |

`format=json` wraps the rows as `{"value": [...]}` with nulls for empty
cells and numbers for numeric columns; `format=csv` quotes every cell and
gives empty strings. The CSV is served as bare `text/csv` **with no charset**,
and the body is UTF-8 — a client that reads it as the HTTP default
(ISO-8859-1) mangles every accented name (`FKG Dentaire SÃ rl`). Decode the
bytes as UTF-8 explicitly. This package's client does; the JSON read API is
unaffected because it declares its charset.

Three endpoints exist. Everything else tried — `/actor`, `/certificate(s)`,
`/basicudi`, `/device(s)`, `/sscp`, `/nomenclature`, `/emdn`, `/swagger`,
`/openapi` — returns 404.

| Endpoint | Rows | Filters |
|---|---|---|
| `GET /udi` | one per UDI-DI | see below |
| `GET /actors` | one per economic operator | `ACTOR_ID`, `NAME`, `ABBREVIATED_NAME`, `ACTOR_TYPE`, `CA_NAME`, `CA_ACTOR_ID`, `ACT_COUNTRY_ISO2_CODE` |
| `GET /reference` | one per (code, id, language) | `CODE`, `ID`, `LANGUAGE` |

**A hard 1,000-row cap, with no pagination.** Every endpoint returns at most
1,000 rows. `$top`, `$skip`, `limit`, `offset`, `page`, `pageSize` and `skip`
all return HTTP 400; the OpenAPI file declares none. A response of exactly
1,000 rows must be presumed truncated and the query partitioned further —
there is no way to ask for the rest. This is the single most important
property of the service: a truncated response looks complete.

**Unknown parameters are refused, not ignored.** Unlike the SPA read API,
which silently drops a parameter it does not recognise and returns the whole
register, this endpoint answers any unrecognised name — and any column it does
not accept as a filter — with **HTTP 400 and an empty body**. That is safer,
with one trap: a client that treats every non-200 as "no rows" reads a 400 as
"this manufacturer has no devices". An earlier version of this package's own
filter list called several columns "inert — returns empty" for exactly that
reason; they were being refused.

**No timestamp anywhere.** No column carries a registration, version or
extraction date; the response headers are `Date`, `x-ms-correlation-id` and
`Request-Context` only — no `Last-Modified`, no `ETag`. The as-of date of a
Data Lake pull is the date you pulled it, and nothing in the data will
contradict a wrong one.

Filters combine with AND. Requests take seconds and a full 1,000-row CSV page
is under a megabyte. Rate-limit yourself anyway (this package holds a floor of
half a second between requests); nothing here is documented as permitted at
any particular rate.

## `GET /udi`

### Filters

Verified by sending each column name as a parameter with a value taken from
the data and observing the rows returned. The accepted set is exactly the one
the OpenAPI file declares for `/udi`; the semantics are not in the file.

Accepted:

| Parameter | Match | Note |
|---|---|---|
| `MF_SRN` | exact | the practical partition key: one request per manufacturer |
| `BASIC_UDI` | exact | one device model, all its UDI-DIs |
| `PRIMARY_DI` | exact | one UDI-DI |
| `SPECIAL_DEVICE_TYPE_ID` | exact | `-43` MDR software, `-47` IVDR software, `-1192` MDD (legacy) software, `-1202` IVDD (legacy) software, `-44` standard soft contact lenses, … |
| `RISK_CLASS_ID` | exact | negative reference id — Class IIa is `-204`, see the vocabulary below |
| `APPLICABLE_LEGISLATION_ID` | exact | `-197` MDR, `-198` IVDR, `-53` MDD, `-54` AIMDD, `-55` IVDD |
| `PLACED_ON_THE_MARKET_ID` | exact | reference id; the `/reference` labels for it are country names |
| `NOMENCLATURE_CODE` | exact, **stored with a leading space** | ` Z12110102` matches; `Z12110102` returns zero rows with HTTP 200; `Z12` (prefix) returns zero rows. Multi-code devices store a comma-separated list (` C0104010101,C0104020101`), which only the whole string matches. |
| `DEVICE_NAME` | exact on the whole field, case-insensitive | `finncomfort` matches `FinnComfort`; no substring |
| `TRADE_NAME` | exact on the whole field, case-insensitive | a prefix of the trade name does not match |
| `REFERENCE` | exact, case-insensitive | |
| `DEVICE_MODEL`, `MEDICAL_PURPOSE` | accepted | match type not characterised |

Refused (HTTP 400, empty body): every other column — `DEVICE_CRITERION`,
`DEVICE_STATUS_TYPE_ID`, `LATEST_VERSION`, `STATUS_ID`, `MF_NAME`, `UUID`,
`ID`, both ULIDs, `AR_SRN`, `AR_NAME`, `MULTI_COMPONENT_ID`, `VERSION_NUMBER`,
the actor-name JSON columns, the DI list columns and every boolean flag
(`STERILE`, `IMPLANTABLE`, `ACTIVE`, …) — and any name that is not a column,
including `MF_COUNTRY_ISO2_CODE`, which people reach for and which does not
exist. There is no manufacturer-country column: derive it from the SRN prefix
or join `/actors`.

A wrong *value* for an accepted filter — `RISK_CLASS_ID=-9999`,
`MF_SRN=XX-MF-000000000` — returns HTTP 200 with an empty body, exactly like a
manufacturer with no registrations. Reference ids are negative integers and
must be looked up, never guessed; an earlier attempt guessed them and was
wrong on every one, and the resulting empty responses were misread as
"this filter does nothing".

The software slices for the IVDR and the two legacy directives have fitted
under the cap; the MDR software slice has not, and must be enumerated by
manufacturer or split on `RISK_CLASS_ID`. Where a slice is under the cap its
row count has matched the read API's `deviceTypes` count for the same code on
the same day, which is a usable cross-source consistency check.

### The columns

```
ID, UDI_DI_DATA_ULID, UUID, TRADE_NAME, REFERENCE, PLACED_ON_THE_MARKET_ID,
LATEST_VERSION, CMR_SUBSTANCE, ENDOCRINE_DISRUPTOR, LATEX, REPROCESSED, STERILE,
STERILIZATION, NEW_DEVICE, VERSION_NUMBER, PRIMARY_DI, BASIC_UDI,
BASIC_UDI_DATA_UUID, BASIC_UDI_DATA_ULID, ACTIVE, ADMINISTERING_MEDICINE,
ANIMAL_TISSUES, COMPANION_DIAGNOSTICS, HUMAN_TISSUES, IMPLANTABLE, KIT,
MEASURING_FUNCTION, MICROBIAL_SUBSTANCES, NEAR_PATIENT_TESTING, REUSABLE,
SELF_TESTING, SPECIAL_DEVICE_TYPE_ID, REAGENT, MULTI_COMPONENT_ID, INSTRUMENT,
PROFESSIONAL_TESTING, SUTURES, HUMAN_PRODUCT, MEDICINAL_PRODUCT, DEVICE_NAME,
DEVICE_MODEL, RISK_CLASS_ID, APPLICABLE_LEGISLATION_ID, DEVICE_CRITERION,
MEDICAL_PURPOSE, NOMENCLATURE_CODE, MF_SRN, MF_NAME, DEVICE_STATUS_TYPE_ID,
MF_ACTOR_NAMES, ACTOR_ABBREVIATED_NAMES, STATUS_ID, AR_NAME, AR_SRN,
AR_ACTOR_NAMES, UNIT_OF_USE_DI, DIRECT_MARKETING_DI, SECONDARY_DI,
CONTAINER_PACKAGE_DIS, SUBSTATUSES
```

Conventions, from unfiltered samples and the filtered pulls above:

- **Identifiers.** `UUID` is the UDI-DI uuid the read API and the public
  interface use (`#/screen/search-device/{UUID}`); `BASIC_UDI_DATA_UUID` is
  the Basic UDI-DI's. Both also carry a ULID (`UDI_DI_DATA_ULID`,
  `BASIC_UDI_DATA_ULID`), which is what the read API's version-history and
  SSCP endpoints key on. The manufacturer's *actor* uuid — needed for the
  interface's economic-operator page — is not here; it costs one Basic UDI-DI
  detail request on the read API.
- **Reference-coded columns are negative integers**: `RISK_CLASS_ID`,
  `APPLICABLE_LEGISLATION_ID`, `DEVICE_STATUS_TYPE_ID`,
  `SPECIAL_DEVICE_TYPE_ID`, `PLACED_ON_THE_MARKET_ID`, `MULTI_COMPONENT_ID`,
  `STATUS_ID`. Decode them through `/reference` (below); this package's
  `eudamed.reference` does.
- **Booleans are `1`, `0` or empty.** Empty means the flag was not applicable
  or not entered, not false.
- **`DEVICE_CRITERION`** takes three values: `STANDARD` (MDR/IVDR
  registration), `LEGACY` (Art. 120 transitional device registered under the
  MDD/AIMDD/IVDD) and `SPP` (system or procedure pack, Art. 22). This is the
  pathway variable, and it is populated here for free where the read API's
  search response nulls it.
- **`LATEST_VERSION` has been `1` on every row seen**, while `VERSION_NUMBER`
  varies. The export appears to carry only the current version of each
  UDI-DI; the version-history endpoint of the read API is the only route to
  earlier ones.
- **`NOMENCLATURE_CODE` carries a leading space** on every value, and a device
  with several EMDN codes stores them comma-separated in one cell. Strip and
  split before joining to an EMDN term list. Codes are not ordered
  software-first or otherwise.
- **`MF_ACTOR_NAMES` and `AR_ACTOR_NAMES` are JSON documents inside a CSV
  cell** — `{"texts":[{"language":{"isoCode":"en",…},"text":"…"}]}`, the
  multilingual name structure. `MF_NAME` and `AR_NAME` are the plain strings.
- **`CONTAINER_PACKAGE_DIS`** is a comma-separated list of DIs.
- **`MEDICAL_PURPOSE`** is free text and populated on a small minority of
  rows, with values from a paragraph of intended-purpose prose to `No`. It is
  the closest thing to an intended-purpose field in either public source, and
  far too sparse to screen on.
- **Names are often empty**: a sizeable share of rows have no `DEVICE_NAME`,
  and more have no `TRADE_NAME`. Some rows under one Basic UDI-DI carry a
  device name and others do not.
- `SUBSTATUSES` has been empty on every row seen; `STATUS_ID` takes very few
  values.

### `GET /reference` — decoding the ids

Columns `ID, CODE, LANGUAGE, VALUE`; filters `CODE`, `ID` and `LANGUAGE`,
all exact. Without `LANGUAGE` every EU language comes back in one response,
so pass `LANGUAGE=en` (or filter client-side) or you keep whichever language
was written last. **Always pass `CODE`**: without it the response is the first
1,000 rows of every vocabulary and is truncated — dominated by
`PLACED_ON_THE_MARKET_ID` (countries × languages) — so even the *list of
codes* visible that way is not necessarily complete. `ID` alone matches
across vocabularies (`-204` is both a risk class and a country). Codes seen:
`PLACED_ON_THE_MARKET_ID`, `RISK_CLASS_ID`, `APPLICABLE_LEGISLATION_ID`,
`SPECIAL_DEVICE_TYPE_ID`, `DEVICE_STATUS_TYPE_ID`, `MULTI_COMPONENT_ID`,
`STATUS_ID`. Per-code responses fit well under the cap.

English values:

| `RISK_CLASS_ID` | | `APPLICABLE_LEGISLATION_ID` | |
|---|---|---|---|
| `-203` | Class I | `-197` | MDR (Regulation (EU) 2017/745) |
| `-204` | Class IIa | `-198` | IVDR (Regulation (EU) 2017/746) |
| `-205` | Class IIb | `-53` | MDD (Directive 93/42/EEC) |
| `-10` | Class III | `-54` | AIMDD (Directive 90/385/EEC) |
| `-199` | Class A | `-55` | IVDD (Directive 98/79/EC) |
| `-200` | Class B | `-3020` | NONE |
| `-201` | Class C | `-3021` | UNKNOWN |
| `-202` | Class D | | |
| `-154` | AIMDD | **`DEVICE_STATUS_TYPE_ID`** | |
| `-155` | IVD Annex II List A | `-11` | On the EU market |
| `-156` | IVD Annex II List B | `-12` | No longer placed on the EU market |
| `-157` | IVD General | `-790` | Not intended for the EU market |
| `-219` | IVD devices for self-testing | | |

`SPECIAL_DEVICE_TYPE_ID`: `-43` Software (MDR), `-47` Software (IVDR) — the
two rows carry the same English label and are told apart only by their
untranslated fallbacks in other languages (`refdata.special-mdr-device-type.software`
vs `…ivd…`) — `-44` Standard soft contact lenses, `-46` Standard rigid gas
permeable (RGP) contact lenses, `-1188` Made-to-order soft contact lenses,
`-3030` Made-to-order RGP contact lenses, `-1189` Spectacle frames, `-1190`
Spectacle lenses, `-1191` Ready-made reading spectacles. **`-1192` and
`-1202` — the software flags on legacy MDD and IVDD devices, which do occur
in `/udi` — have no row in `/reference` at all**, in any language. A decoder
built only from `/reference` will show them as raw integers.

Note that the register does not separate Class Is, Im or Ir from Class I in
either source; the read API 400s on `refdata.risk-class.class-is`.

## `GET /actors`

One row per registered economic operator — manufacturers, importers,
authorised representatives, system/procedure-pack producers and notified
bodies — capped at 1,000 like everything else. Columns:

```
ACTOR_ID, NAME, ABBREVIATED_NAME, STATUS_FROM_DATE, STATUS, ACTOR_TYPE,
SPONSOR_TYPE, EUROPEAN_VAT_NUMBER, VERSION, ACT_COUNTRY_NAME,
ACT_COUNTRY_ISO2_CODE, ACT_COUNTRY_TYPE, ACT_EMAIL, ACT_TELEPHONE, ACT_WEBSITE,
ACT_ADDR_BUILDING_NUMBER, ACT_ADDR_STREET_NAME, ACT_ADDR_POST_BOX,
ACT_ADDR_POSTAL_ZONE, ACT_ADDR_CITY_NAME, ACT_ADDR_COUNTRY_NAME,
ACT_ADDR_COUNTRY_CODE, ACT_ADDR_COUNTRY_TYPE, PRRC_FIRST_NAME, PRRC_FAMILY_NAME
```

| Parameter | Match | Note |
|---|---|---|
| `ACTOR_ID` | exact | the SRN (`NL-MF-000027730`); for notified bodies the four-digit NB number (`2460`) |
| `ACTOR_TYPE` | exact | `Manufacturer`, `Importer`, `Authorised Representative`, `System/Procedure Pack Producer`, `Notified Body` (the last fits under the cap) |
| `ACT_COUNTRY_ISO2_CODE` | exact | any populous country hits the cap; combine with `ACTOR_TYPE` |
| `NAME`, `ABBREVIATED_NAME` | exact, whole field | `UROMASTER GmbH` matches, `UROMASTER` does not |
| `CA_NAME`, `CA_ACTOR_ID` | accepted | competent-authority name and id; not otherwise characterised |

These are exactly the parameters the OpenAPI file declares. `STATUS`,
`VERSION`, `SRN` and anything else → HTTP 400. There is no uuid
column, so this endpoint cannot by itself produce a link into the public
interface's economic-operator page; the read API's Basic UDI-DI detail carries
`manufacturer.uuid`.

**This endpoint returns personal data.** `PRRC_FIRST_NAME` and
`PRRC_FAMILY_NAME` name the person responsible for regulatory compliance,
and `ACT_EMAIL` and `ACT_TELEPHONE` are frequently an individual's. The
GDPR applies to whatever you build from it regardless of the source being
public; strip those columns before depositing or redistributing an extract.
`ACT_WEBSITE` is free text and frequently unusable as a URL (`n/a`, `-`,
`tbd`, two domains in one cell, trailing whitespace).

`ACTOR_ID` prefixes mix conventions the way SRNs do everywhere in EUDAMED:
`GB-` and `UK-` both occur, as do `GR-` and `EL-`, and `XI-` marks Northern
Ireland. Normalise before counting by country.

## What the Data Lake does not carry, and how it can be incomplete

This is the section to read before treating a Data Lake pull as the register.

1. **It lags the live register.** UDI-DIs registered on the day of the query
   have been absent: records sampled from page 0 of the read API's device
   search — which holds the newest registrations — were mostly missing from
   the export by `PRIMARY_DI` while records from deeper pages were all
   present, and records registered late the previous day were present the
   next morning. So the export appears to refresh at least daily, but nothing
   documents the cadence and nothing in the data dates the snapshot. Anything
   registered since the last refresh is invisible here and visible on the
   read API.

2. **Every partition over 1,000 rows is silently truncated.** A manufacturer
   with more than 1,000 UDI-DIs cannot be enumerated by `MF_SRN` alone, and
   a few percent of manufacturers are that large. Split those on
   `RISK_CLASS_ID`, `APPLICABLE_LEGISLATION_ID` or `SPECIAL_DEVICE_TYPE_ID`,
   or fall back to the read API. A pipeline that does not check for exactly
   1,000 rows will report those manufacturers as complete.

3. **A partition-based harvest is only as complete as its partition list.**
   Pulling by `MF_SRN` retrieves nothing for a manufacturer that was not on
   the list, and the endpoint gives no way to enumerate manufacturers
   (`/actors` is capped too). A same-day comparison once found a noticeable
   share of devices discovered through the read API absent from an `MF_SRN`
   harvest; on re-check weeks later every sampled one was present, and most
   of the gap traced to manufacturers the harvest had never queried plus
   partitions over the cap — not to the Data Lake. **Structural omissions
   from the export have not been demonstrated; nor has their absence.**
   Compare a sample against the read API before claiming either.

4. **Current versions only.** No history, no superseded records, no
   registration or update timestamps on any row.

5. **Whole subject areas are missing.** No certificates, notified-body
   linkage, SSCP references, clinical-investigation links, manufacturer
   country, actor uuids, or per-language names beyond the JSON blobs — all of
   which the read API's Basic UDI-DI detail record carries.

6. **The reference vocabulary is incomplete.** `/reference` has no rows for
   the legacy software type ids `-1192` and `-1202`, and its uncoded listing
   is itself capped, so the set of `CODE`s is only known to be at least the
   seven listed above.

7. **No intended purpose worth screening on.** `MEDICAL_PURPOSE` is populated
   on a small minority of rows.

## Where the two sources fit together

The read API can discover — it filters by EMDN prefix, risk class,
legislation, status, device type and pathway, and its search response carries
`manufacturerSrn` for free — but its search response nulls the device name,
EMDN code and pathway, so each of those costs a detail request per device.
The Data Lake cannot page and cannot filter by EMDN prefix, but returns those
fields for a whole manufacturer in one request. Discover on the read API,
bulk-pull by `MF_SRN` on the Data Lake, and complete from the read API's
detail endpoints whatever the Data Lake truncated or had not yet received.
Record which source each row came from; they are the same register at
different moments.
