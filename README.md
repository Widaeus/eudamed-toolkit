# eudamed-toolkit

A rate-limited, logged client for the public read API behind EUDAMED, the EU
database on medical devices, plus a client for the Commission's Data Lake bulk
CSV endpoint and the tooling to turn either into a reproducible export.

This is an **unofficial** client. The European Commission publishes no
specification for the API this package talks to; everything it knows about
that API's behaviour was established empirically against the live service and
is documented in [`docs/api-reference.md`](docs/api-reference.md). This
project is not affiliated with, endorsed by, or in any way connected to the
European Commission.

## Installation

```bash
pip install eudamed-toolkit
```

Parquet export needs an extra: `pip install eudamed-toolkit[parquet]`.

## Quick start

```bash
# How many UDI-DI records fall under EMDN branch Z1203?
eudamed search --cnd-code Z1203 --count

# Stream that same filtered set to a JSONL file. A manifest recording the
# query and a SHA-256 of the output is written to z1203.jsonl.manifest.json,
# named after the output so a second export into the same directory cannot
# overwrite the first one's provenance.
eudamed export z1203.jsonl --cnd-code Z1203

# Look up one device by its UDI-DI uuid, and get the link into the public
# interface for it.
eudamed device 8dc77343-25b8-493b-b7f7-5c2bdebcf6b1
```

## Resuming an interrupted export

A filtered export walks the search endpoint page by page, and a page that
cannot be fetched raises rather than truncating the file silently — so a crawl
that dies at page 140 of 149 would otherwise have to start again from page 0.
Each completed page is recorded in `<out>.progress.json`, and `--resume`
carries on from the next unfetched page:

```bash
eudamed export z1203.jsonl --cnd-code Z1203            # dies at page 140
eudamed export z1203.jsonl --cnd-code Z1203 --resume   # picks up at page 140
```

A checkpoint whose filters, format, page size or `--enrich` setting do not
match the current command is refused, naming the field that differs; resuming
one query into another query's file would blend two result sets into one file
carrying one manifest. Without `--resume` an existing checkpoint is ignored
and the output overwritten. A completed export deletes its checkpoint.

**A resumed export is not a point-in-time snapshot.** The search endpoint has
no server-side sort and the register changes daily, so page boundaries do not
hold between runs: one device registered or withdrawn while the crawl is
paused shifts every later record by one position. The resulting file is
stitched together from two or more moments and can duplicate records across
the seam or miss them. The checkpoint stores the uuids of the last page
written and skips them on resume, which catches drift smaller than one page —
the common case — but larger drift cannot be detected through this API at
all: there is no "changed since" filter, and `lastUpdateDate` is null in every
search response. The manifest therefore carries `resumed` and `resume_points`,
so a stitched extract is distinguishable from a single-pass one on disk. If
the extract has to be a snapshot, re-run it from scratch.

## Three things the API does that will cost you a day

**It silently ignores parameters it does not recognise.** A misspelled or
unsupported filter is not rejected — it returns the whole 2.98-million-record
register with HTTP 200. A typo does not fail loudly; it quietly replaces your
result set with everything. This client refuses any filter name that has not
been individually verified to change `totalElements` (see
`VERIFIED_DEVICE_FILTERS` in `eudamed.client`); it will raise `ValueError`
rather than let an unverified name reach the API. There is no CLI escape hatch
around this — a flag only exists for a name on that list.

**`name=` searches the manufacturer's name, not the device's.** It matches a
substring over a concatenated field that includes the manufacturer's
registered name, so `--name Siemens` will surface devices made by *Varian
Medical Systems*, a Siemens Healthineers company. It is a useful recall net,
but treating a hit as evidence the device itself is named that is a mistake
this API makes easy to walk into.

**A missing record returns 302, never 404.** EUDAMED answers "no such record"
by redirecting to an internal page-not-found route with HTTP 302. Follow that
redirect and you'll get a 200 and an HTML page, which looks like success. This
client sets `allow_redirects=False` and treats any redirect as a miss
(`None`), so a lookup failure surfaces as a lookup failure.

## An outage is not an empty result

A request that could not be answered raises `eudamed.RequestFailed`; it never
comes back as zero records, zero devices or an empty page. The distinction the
whole package turns on is between *the register says there is nothing* and *we
could not find out*:

- A search matching no devices yields nothing, and `count_devices` returns `0`.
- A page that 503s mid-crawl raises, so `eudamed export` cannot write a
  plausible-looking partial extract — with a manifest and a SHA-256 attesting
  to it — and exit 0.
- A Data Lake query for a manufacturer with no registrations is an empty
  `Result`; one that could not be run raises, and `harvest` reports it under
  `failed_srns` rather than counting it as pulled.

`eudamed reference` is the deliberate exception: a failed fetch there yields
an empty map for that code rather than raising, because an unrecognised id
then falls back to the raw id, which is visibly not a label, so nothing
downstream is silently wrong — and it lets a rebuild run offline from cache.

The CLI exits 0 on success, 1 for a record that does not exist, 2 for a usage
error and **3 for a request that failed**, except `reference`, which exits 0
even under total network failure. A script can otherwise tell "nothing is
there" from "we could not find out".

## EMDN traversal is unverified

`eudamed nomenclature walk` is built against `GET /devices/nomenclatures/`,
which **returned HTTP 500 to every form tried on 2026-08-11** (bare, with the
full page parameter set, with `code=Z`, and at `/devices/nomenclatures/roots`;
confirmed twice that day). The command is shipped because the path is real and
appears in the interface's own endpoint map, but its response handling has
never been exercised against a live response, and today it will raise
`RequestFailed` rather than return a tree. `eudamed nomenclature sweep`, which
counts devices per EMDN code through the search endpoint, is unaffected and
works. See [`docs/api-reference.md`](docs/api-reference.md).

See [`docs/api-reference.md`](docs/api-reference.md) for the rest — the
endpoint map, the full verified and non-working filter lists, the endpoints
that 500 or 302 unconditionally, the fields the search response always returns
`null`, the throughput and throttling measurements, and the interface routes
for linking back into the public site.

## Basic UDI-DI vs UDI-DI

EUDAMED's unit of analysis is not obvious from the API alone. A **Basic
UDI-DI** identifies a device *model*; each **UDI-DI** under it identifies a
packaging or trade-item variant of that same model (different pack sizes,
sterile vs non-sterile, and so on). Counting UDI-DIs when you mean to count
devices inflates your figure — the observed inflation factor is around **2.5**
UDI-DIs per Basic UDI-DI. Decide which one you're counting before you count
anything, and say which one you did.

## Rate limits and the request log

The client enforces a minimum interval between requests (0.4 s by default,
`--min-interval` on the CLI), held across threads so raising concurrency never
raises the request rate. Sustained rates above roughly 4 requests/second have
been observed to produce HTTP 429 within a minute or two, and EUDAMED never
returns a `Retry-After` header, so the client cannot learn the correct backoff
from the service — it widens its own interval after a 429 and eases it back
down only after a run of sustained success.

Detail lookups (`device`, `actor`, the Basic UDI-DI records `--enrich` merges
in) are cached on disk under `--cache-dir`; search pages never are, because
they are volatile. **The cache does not expire.** The register changes daily —
registrations are added, statuses change — so a cached record can be
arbitrarily old, and a re-run of a months-old extraction will happily rebuild
it from months-old responses. Pass `--no-cache` for a fresh pull, or delete the
cache directory. A cached record's own contents carry no fetch timestamp — only
the file's modification time and the request log do.

Every request — URL, parameters, status, byte count, elapsed time — is
appended to a JSONL log (`logs/requests.jsonl` by default, `--log` to change
it), so a run can be reconstructed and audited after the fact. Please rate-limit
politely; this is shared public infrastructure, and it is logged on the
Commission's side too. Pass `--contact you@example.org` to identify yourself
in the User-Agent header.

## Licence and citation

MIT. See [`LICENSE`](LICENSE).

If this package is useful in published work, please cite the repository:

```
Widaeus, J. eudamed-toolkit: an unofficial client for the EUDAMED public API.
https://github.com/Widaeus/eudamed-toolkit
```
