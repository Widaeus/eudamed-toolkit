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
  tree and per-code device counts.
- `eudamed.export` — streams a filtered device search to JSONL, CSV or
  Parquet without holding the result set in memory, writing a manifest
  alongside the output.
- `eudamed.provenance` — snapshot manifests (content hashes, commit, platform)
  and removal of manufacturer contact fields before deposit or redistribution.
- `eudamed.urls` — construction of links into the public EUDAMED interface
  from UDI-DI and actor uuids.
- `eudamed.cli` — the `eudamed` command-line entry point (`search`, `device`,
  `actor`, `export`, `nomenclature walk`/`sweep`, `reference`), with filter
  flags generated from `VERIFIED_DEVICE_FILTERS`.
- `docs/api-reference.md` — the empirical API reference this package is built
  from.

[0.1.0]: https://github.com/Widaeus/eudamed-toolkit/releases/tag/v0.1.0
