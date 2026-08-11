"""Client and extraction tools for the EU database on medical devices.

EUDAMED publishes device registrations under the transparency provisions of
Regulation (EU) 2017/745. This package provides a rate-limited client for its
public read API, a client for the Commission's Data Lake bulk endpoint, and the
tooling needed to turn either into a reproducible extract.

The API is undocumented and has one property that makes it dangerous for
research use: it silently ignores query parameters it does not recognise, so a
misspelled filter returns the entire register with a 200 status instead of an
error. This client refuses any filter name it has not verified. See
docs/api-reference.md.

The same principle governs failure: a request that could not be answered
raises ``RequestFailed`` rather than returning an empty result, because an
outage written to disk as "no matching devices" is indistinguishable from the
truth once the run is over.
"""

from __future__ import annotations

from eudamed.errors import EudamedError, RequestFailed

__version__ = "0.1.0"


def user_agent(contact: str | None = None, agent: str | None = None) -> str:
    """The User-Agent every request in this package identifies itself with.

    Shared by all three HTTP surfaces (public API, Data Lake, reference
    values) so that none of them can quietly fall back to the default
    ``requests`` agent on shared public infrastructure. ``agent`` overrides
    the package's own string; ``contact`` is appended either way.
    """
    agent = agent or f"eudamed-toolkit/{__version__} (+https://github.com/Widaeus/eudamed-toolkit)"
    if contact:
        agent = f"{agent}; contact: {contact}"
    return agent


__all__ = ["EudamedError", "RequestFailed", "__version__", "user_agent"]
