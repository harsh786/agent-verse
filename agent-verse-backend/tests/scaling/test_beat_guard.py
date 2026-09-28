"""Regression tests for the beat overlap guard (app/scaling/beat_guard.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.scaling.beat_guard import beat_task_guard


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int, nx: bool) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    def delete(self, key: str) -> None:  # pragma: no cover - must not be used
        raise AssertionError("unconditional DEL must not be used")


def _guarded(fake: _FakeRedis, body: Any) -> Any:
    @beat_task_guard(lock_ttl_seconds=10)
    def task() -> Any:
        return body()

    return task


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    from app.scaling.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "broker_url", "redis://x/0")
    r = _FakeRedis()
    return r


def test_task_exception_runs_the_task_once_not_twice(fake: _FakeRedis) -> None:
    calls: list[int] = []

    def body() -> None:
        calls.append(1)
        raise ValueError("task failed")

    with patch("redis.from_url", return_value=fake), pytest.raises(ValueError):
        _guarded(fake, body)()
    assert calls == [1]  # used to be re-run by the "guard failure" fallback
    assert fake.store == {}  # lock released


def test_expired_run_does_not_release_a_newer_runs_lock(fake: _FakeRedis) -> None:
    def body() -> str:
        # Our lock expired mid-run and another replica took it.
        fake.store["beat_guard:task"] = "someone-elses-token"
        return "done"

    with patch("redis.from_url", return_value=fake):
        assert _guarded(fake, body)() == "done"
    assert fake.store == {"beat_guard:task": "someone-elses-token"}


def test_overlap_is_skipped(fake: _FakeRedis) -> None:
    fake.store["beat_guard:task"] = "held"
    with patch("redis.from_url", return_value=fake):
        assert _guarded(fake, lambda: "ran")() == {"skipped": True, "reason": "overlap"}
