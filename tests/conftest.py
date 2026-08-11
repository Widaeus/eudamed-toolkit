"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = content or json.dumps(payload or {}).encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def fake_session(monkeypatch):
    """Replace requests.Session.get with a scripted sequence of responses."""

    class Recorder:
        def __init__(self):
            self.calls = []
            self.responses = []

        def queue(self, *responses):
            self.responses.extend(responses)

        def __call__(self, url, params=None, timeout=None, allow_redirects=None):
            self.calls.append({"url": url, "params": params,
                               "allow_redirects": allow_redirects})
            return self.responses.pop(0) if self.responses else FakeResponse(200, {})

    recorder = Recorder()
    monkeypatch.setattr("requests.Session.get", lambda self, *a, **k: recorder(*a, **k))
    return recorder
