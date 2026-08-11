"""Shared pytest configuration.

An empty test suite is the expected state early in this project's life; treat
pytest's "no tests collected" exit code as success rather than failure.
"""

from __future__ import annotations

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK
