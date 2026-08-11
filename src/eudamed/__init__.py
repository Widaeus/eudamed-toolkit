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
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
