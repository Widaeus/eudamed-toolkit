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

# Stream that same filtered set to a JSONL file, with a manifest recording
# the query and a SHA-256 of the output.
eudamed export z1203.jsonl --cnd-code Z1203

# Look up one device by its UDI-DI uuid, and get the link into the public
# interface for it.
eudamed device 8dc77343-25b8-493b-b7f7-5c2bdebcf6b1
```

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
