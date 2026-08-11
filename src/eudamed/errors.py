"""Exceptions raised when a request could not be answered.

The distinction this package is built on is between *the register says there
is nothing* and *we could not find out*. The first is a legitimate empty
result: an empty ``content`` list, a zero ``totalElements``, a 302 to the
page-not-found route, a Data Lake query with no matching rows. The second is
an outage, a throttle that outlasted the retries, a 500, a truncated
connection — and it must never be returned as an empty result, because an
empty result is indistinguishable from the first case once it has been
written to disk.

``RequestFailed`` is what the second case raises. It carries enough to
identify the request that failed — URL, parameters, the last status code seen
and how many attempts were made — so that a caller can log it, retry it, or
record it as a gap rather than as a zero.
"""

from __future__ import annotations

from typing import Any


class EudamedError(Exception):
    """Base class for every error this package raises deliberately."""


class RequestFailed(EudamedError):
    """A request did not produce a usable response after retries.

    Raised rather than returning an empty result, so that "the service did
    not answer" can never be mistaken for "the register holds no such
    records".
    """

    def __init__(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        status: int | None = None,
        attempts: int = 1,
        reason: str | None = None,
    ) -> None:
        self.url = url
        self.params = dict(params or {})
        self.status = status
        self.attempts = attempts
        self.reason = reason
        detail = f"HTTP {status}" if status is not None else (reason or "no response")
        if reason and status is not None:
            detail = f"HTTP {status}: {reason}"
        super().__init__(
            f"request failed after {attempts} attempt(s): {url} "
            f"params={self.params!r} ({detail}). This is not an empty result -- "
            "the register was not reached, so nothing can be concluded about "
            "how many records match."
        )
