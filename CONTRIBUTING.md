# Contributing

## Development install

```bash
git clone https://github.com/Widaeus/eudamed-toolkit
cd eudamed-toolkit
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

Run all three before opening a pull request; CI runs the same three.

```bash
ruff check src tests
mypy src
pytest
```

## Adding a filter

**A new entry in `VERIFIED_DEVICE_FILTERS` (in `src/eudamed/client.py`)
requires evidence that it changes `totalElements` against the live API.** This
is the one rule in this codebase that cannot be relaxed: the API silently
ignores query parameters it does not recognise and returns the whole 2.98
million-record register with HTTP 200, so a filter added on the strength of
what a form field is *named*, rather than what it measurably does, turns
every count taken with it into a false one.

To produce that evidence, compare `totalElements` for the same query with and
without the candidate parameter. `EudamedClient.get` bypasses the allow-list
(only `search_devices` enforces it), so this is a two-command check:

```bash
python -c "from eudamed.client import EudamedClient as C; print(C(cache_dir=None).get('devices/udiDiData', {'page': 0, 'pageSize': 1, 'size': 1, 'iso2Code': 'en', 'languageIso2Code': 'en'})['totalElements'])"
python -c "from eudamed.client import EudamedClient as C; print(C(cache_dir=None).get('devices/udiDiData', {'page': 0, 'pageSize': 1, 'size': 1, 'iso2Code': 'en', 'languageIso2Code': 'en', '<candidate>': '<value>'})['totalElements'])"
```

If the two numbers differ, the parameter filters and can be added, with the
value used and the resulting count recorded in the pull request. If they
match, it doesn't — add it to the "verified NOT to work" list in
`docs/api-reference.md` instead, so nobody re-tests it. Either way, add a
matching entry to `FILTER_HELP` in `src/eudamed/cli.py` describing the
parameter's real semantics (prefix match, substring, exact, full refdata
code), and to the CLI's own tests.

## Reporting an undocumented endpoint

If you find an endpoint or parameter not covered in `docs/api-reference.md`,
please open an issue with:

- the exact request (method and path),
- the parameters used, and
- the observed `totalElements` (or status code, if it's an error).

Without those three, a report can't be turned into a documented, tested
behaviour.
