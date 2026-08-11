# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/Widaeus/eudamed-toolkit/releases/tag/v0.1.0
