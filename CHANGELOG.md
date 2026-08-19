# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-08-19

First stable release. The public interface of `eudamed.client`,
`eudamed.datalake`, `eudamed.export`, `eudamed.reference`, `eudamed.urls`,
`eudamed.provenance` and the `eudamed` command is now covered by semantic
versioning.

### Added

- `eudamed.export` — resumable exports. Every completed page is recorded in
  `<out>.progress.json`; an export that raises leaves that checkpoint and its
  partial output in place, and `export_devices(..., resume=True)` (`--resume`
  on the CLI) continues from the next unfetched page rather than starting
  over. A checkpoint whose `filters`, `fmt`, `page_size` or `enrich` differ
  from the current call is refused, naming the field that differs. A
  completed export deletes its checkpoint. **A resumed export is not a
  point-in-time snapshot**: the search endpoint has no server-side sort and
  the register changes daily, so the result is stitched from two or more
  moments. The checkpoint stores the uuids of the last page written and skips
  them on resume, which catches drift smaller than one page; the manifest
  records `resumed` and `resume_points` so a stitched extract can be told
  from a clean one on disk.
- `docs/datalake-reference.md` — an empirical reference for the DG SANTE
  Data Lake: how it is reached, its three endpoints, the row cap, the
  accepted and refused filters for `/udi`, the columns and their value
  conventions, `/actors` (with its personal-data columns) and `/reference`
  with the decoded vocabularies, and a closing section on what the export
  does not carry — the lag behind the live register, cap truncation,
  current-versions-only, the missing subject areas and the incomplete
  reference vocabulary.
- `docs/api-reference.md` — the refdata vocabularies for risk class,
  legislation, status and special device type, including the values that do
  not exist and the legacy software device-type codes; `deviceCriteria=SPP`;
  the certificate, refused-application and notified-body endpoints with their
  fields and verified filters; the `medicalPurpose` and certificate-scope
  `intendedPurpose` fields that qualify the no-intended-purpose finding;
  timestamps on the detail records; SRN conventions; the EMDN source file;
  the Commission's own documentation and its broken links.
- `skills/eudamed-toolkit/SKILL.md` — a skill file for coding agents that
  use this package.
- `SPECIAL_DEVICE_TYPE` gained `mdd_software` and `ivdd_software`, the flags
  on legacy devices, which the reference endpoint has no labels for.

### Changed

- `eudamed.export` walks `client.search_devices` page by page rather than
  `client.iter_devices`, which flattens away the page boundaries a checkpoint
  needs. The client's public interface is unchanged.
- `eudamed.export` checks for `pyarrow` before starting the crawl rather than
  after it.
- `eudamed.reference` asks the reference endpoint for English only
  (`LANGUAGE=en`), which the endpoint accepts, and still filters client-side.
- Both reference documents and the README no longer record counts, timings
  or verification dates. Every figure pulled from EUDAMED is a snapshot as of
  the moment it was pulled and belongs in a manifest, not in a reference; the
  documents record behaviour, which is what a client can be built against.

### Fixed

- `eudamed.datalake` and `eudamed.reference` decoded the Data Lake's CSV
  through `requests`' default, which — because the service sends `text/csv`
  with no charset — is ISO-8859-1. Every non-ASCII character came back
  mangled. Both now decode the body as UTF-8.
- `eudamed.datalake` listed `RISK_CLASS_ID` as a filter the endpoint accepts
  and ignores. It filters (Class IIa is `-204`), as do
  `APPLICABLE_LEGISLATION_ID`, `PLACED_ON_THE_MARKET_ID`,
  `NOMENCLATURE_CODE`, `DEVICE_NAME`, `TRADE_NAME`, `REFERENCE`,
  `DEVICE_MODEL` and `MEDICAL_PURPOSE`; all are now in `VERIFIED_FILTERS`,
  which matches the parameter list in the Commission's OpenAPI file for the
  endpoint. The columns the endpoint does not accept are refused with HTTP
  400 and an empty body, not answered with an empty result as the old
  docstring said; they are now `REJECTED_FILTERS` (`INERT_FILTERS` remains
  as an alias) and the `ValueError` says so.
- `DataLakeClient.fetch` sends `NOMENCLATURE_CODE` in the form the export
  stores it, with a leading space; the match is exact and the bare code
  returned zero rows with HTTP 200.

## [0.1.0] - 2026-08-11

### Added

- `eudamed.client` — rate-limited, cached, logged client for the EUDAMED
  public read API, with an allow-list of query filters (`VERIFIED_DEVICE_FILTERS`)
  individually verified to change `totalElements`.
- `eudamed.datalake` — client for the DG SANTE Data Lake bulk CSV endpoint,
  with its own verified-filter allow-list and truncation detection at its
  1,000-row cap.
- `eudamed.reference` — decodes EUDAMED's opaque reference-value IDs (risk
  class, applicable legislation, device status) into English labels via the
  register's own reference endpoint, with an on-disk cache.
- `eudamed.nomenclature` — breadth-first traversal of the EMDN nomenclature
  tree (`walk`) and per-code device counts (`sweep`). **`walk` is unverified
  against a live response:** the endpoint it traverses,
  `GET /devices/nomenclatures/`, returned HTTP 500 for every form tried on
  2026-08-11, so its response-shape handling has never run against real data
  and a live walk currently raises `RequestFailed`. `sweep` goes through the
  device search endpoint and is unaffected.
- `eudamed.errors` — `EudamedError` and `RequestFailed`, raised whenever a
  request could not be answered. Every module but one turns a failed request
  into `RequestFailed` rather than an empty result: an empty search, a zero
  count and a manufacturer with no registrations are real answers, and an
  outage is not reported as any of them. The deliberate exception is
  `eudamed.reference`, which catches a failed fetch and returns an empty map
  for that code instead — an unrecognised id then comes back as the raw id,
  which reads as obviously not a label, rather than a wrong one that looks
  right. The CLI exits 3 on a failed request except for `reference`, distinct
  from 1 for a record that does not exist.
- `eudamed.export` — streams a filtered device search to JSONL, CSV or
  Parquet without holding the result set in memory, writing
  `<out>.manifest.json` alongside the output. The manifest is named after the
  output file, so two exports into one directory keep two provenance records,
  and it hashes only the files that export wrote.
- `eudamed.provenance` — snapshot manifests (content hashes, commit, platform)
  and removal of manufacturer contact fields before deposit or redistribution.
- `eudamed.urls` — construction of links into the public EUDAMED interface
  from UDI-DI and actor uuids.
- `eudamed.cli` — the `eudamed` command-line entry point (`search`, `device`,
  `actor`, `export`, `nomenclature walk`/`sweep`, `reference`), with filter
  flags generated from `VERIFIED_DEVICE_FILTERS` and `--no-cache` for a run
  that must not be served from the (never-expiring) detail cache.
- `docs/api-reference.md` — the empirical API reference this package is built
  from.

[1.0.0]: https://github.com/Widaeus/eudamed-toolkit/releases/tag/v1.0.0
[0.1.0]: https://github.com/Widaeus/eudamed-toolkit/releases/tag/v0.1.0
