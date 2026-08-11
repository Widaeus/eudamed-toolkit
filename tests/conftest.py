"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

import json
import time

import pytest


@pytest.fixture(autouse=True)
def no_backoff_sleeps(monkeypatch):
    """Collapse retry back-off waits so failure paths can be tested at all.

    The retry ladders are deliberately slow -- seconds to a minute between
    attempts against shared public infrastructure. Waiting them out in the
    suite would make testing the failure paths cost more than testing them is
    worth, which is how failure paths end up untested."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, content=b"", text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = content or json.dumps(payload or {}).encode()
        self.text = text

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
