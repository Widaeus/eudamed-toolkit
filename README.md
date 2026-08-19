# eudamed-toolkit

A rate-limited, logged Python client and CLI for the public read API behind
EUDAMED, the EU database on medical devices, plus a client for the
Commission's Data Lake bulk CSV endpoint and the tooling to turn either into
a reproducible, resumable export with a provenance manifest.

This is an **unofficial** client, not affiliated with, endorsed by or
connected to the European Commission.

## Why this exists

The Commission publishes no documentation for the JSON API that the public
EUDAMED interface calls. Its technical documentation page describes the
registration side — data dictionary, XSD schemas, business rules — and links
one OpenAPI file, which covers only the separate bulk (Data Lake) endpoint and
names its parameters without describing their behaviour, the row cap or what
the export omits. The one third-party write-up of the read API documents four
operations and none of the query parameters that actually filter.

Working from what is published, you would conclude the search endpoint cannot
be filtered, would not know that a misspelled filter silently returns the
whole register with HTTP 200, that `name=` searches the manufacturer's name,
that a missing record is a 302 rather than a 404, or that a bulk response of
exactly 1,000 rows is truncated. Each of those turns a query into a wrong
number without an error. This package encodes what was established
empirically against the live service — as an allow-list of verified filters,
as failure modes that raise instead of returning plausible empties, and as
two reference documents — so that the next person does not have to rediscover
it, and so that a count taken from EUDAMED can be reconstructed and audited.

## Installation

```bash
pip install eudamed-toolkit
pip install eudamed-toolkit[parquet]   # Parquet export
```

## Usage

```bash
eudamed search --cnd-code Z1203 --count           # count UDI-DIs in an EMDN branch
eudamed export z1203.jsonl --cnd-code Z1203       # stream them, with a manifest
eudamed export z1203.jsonl --cnd-code Z1203 --resume
eudamed device <udi-di-uuid>                      # one record, with its interface URL
eudamed reference                                 # decode reference-value ids
```

```python
from eudamed.client import EudamedClient
from eudamed.datalake import DataLakeClient

api = EudamedClient(contact="you@example.org")
n = api.count_devices(cndCode="Z1203", riskClassCode="refdata.risk-class.class-iib")
page = api.search_devices(page=0, page_size=300, deviceCriteria="LEGACY")
basic = api.basic_udi_detail(page["content"][0]["uuid"])   # device name lives here

lake = DataLakeClient(contact="you@example.org")
rows = lake.by_manufacturer("DE-MF-000005430")               # rows.truncated flags the cap
```

## Behaviour to know before you count anything

- **Unknown filter names are silently ignored by the API** and return the
  whole register. The client refuses any name not in
  `VERIFIED_DEVICE_FILTERS`, each of which was shown to change
  `totalElements`. There is no override.
- **`name=` matches the manufacturer's name** as part of a concatenated field;
  `tradeName=` is a diacritic-sensitive substring; the device name is not
  searchable and costs one Basic UDI-DI request per device.
- **A missing record is a 302, never a 404.** The client does not follow
  redirects and returns `None` only for that case.
- **A request that could not be answered raises `RequestFailed`.** It is
  never reported as zero devices, an empty page or a manufacturer with no
  registrations. The CLI exits 0 on success, 1 for a record that does not
  exist, 2 for a usage error and 3 for a failed request. `eudamed reference`
  is the one exception: a failed fetch yields an empty map, so an unknown id
  falls back to the raw id, which is visibly not a label.
- **Count Basic UDI-DIs when you mean devices.** A Basic UDI-DI is a device
  model; each UDI-DI under it is a packaging or trade-item variant. Counting
  UDI-DIs overstates the number of devices several-fold.
- **A resumed export is not a point-in-time snapshot.** There is no
  server-side sort and the register changes daily, so `--resume` stitches two
  moments together; the manifest records `resumed` and `resume_points`.
- **The detail cache never expires.** Pass `--no-cache` for a fresh pull.
- **The Data Lake caps every response at 1,000 rows with no paging**, lags the
  live register (registrations since its last refresh are absent), refuses
  unaccepted filters with HTTP 400, and serves UTF-8 without a charset. The client flags a capped result as
  `truncated`, refuses columns the endpoint would reject, and decodes UTF-8.
- **`eudamed nomenclature walk` is unverified.** The endpoint it traverses
  has returned HTTP 500 on every attempt; `sweep`, which counts devices per
  EMDN code through the search endpoint, works.
- **Rate limits are enforced without a `Retry-After` header.** The client
  holds a minimum interval across threads, widens it on 429 and eases it back
  only after sustained success. Pass `--contact` to identify yourself in the
  User-Agent; every request is appended to a JSONL log for audit.

## Documentation

- [`docs/api-reference.md`](docs/api-reference.md) — the read API: endpoint
  map, verified and non-working filters, refdata vocabularies, response
  shapes, certificate and notified-body endpoints, interface routes,
  throttling behaviour.
- [`docs/datalake-reference.md`](docs/datalake-reference.md) — the bulk
  endpoint: parameters, cap, filters, columns and their conventions, the
  reference vocabularies, and what the export does not carry.
- [`skills/eudamed-toolkit/SKILL.md`](skills/eudamed-toolkit/SKILL.md) — a
  skill file for coding agents using this package. Point your agent at it, or
  copy the directory into your agent's skills folder.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to verify and add a filter, and
  how to report an endpoint.

Neither reference records counts or dates: every figure pulled from EUDAMED
is a snapshot as of the moment it was pulled, and belongs in the manifest
next to your extract.

## Licence and citation

MIT. See [`LICENSE`](LICENSE).

```
Widaeus, J. eudamed-toolkit: an unofficial client for the EUDAMED public API.
https://github.com/Widaeus/eudamed-toolkit
```
