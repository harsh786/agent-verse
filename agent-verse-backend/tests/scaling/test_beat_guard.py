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


# ── WF-21: Redis Sentinel, fail closed ────────────────────────────────────────


def test_unreachable_redis_skips_the_run_instead_of_running_unguarded(
    fake: _FakeRedis,
) -> None:
    calls: list[int] = []
    with patch("redis.from_url", side_effect=ConnectionError("down")):
        result = _guarded(fake, lambda: calls.append(1))()
    assert result == {"skipped": True, "reason": "guard_unavailable"}
    assert calls == []


def test_sentinel_broker_url_takes_the_lock_on_the_sentinel_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "broker_url", "sentinel://:s3cret@s1:26379;s2:26380/2")
    monkeypatch.setattr(
        celery_app.conf,
        "broker_transport_options",
        {"master_name": "primary", "sentinel_kwargs": {"socket_timeout": 1}},
    )
    fake = _FakeRedis()
    seen: dict[str, Any] = {}

    class _Sentinel:
        def __init__(self, nodes: Any, sentinel_kwargs: Any, password: Any) -> None:
            seen.update(nodes=nodes, sentinel_kwargs=sentinel_kwargs, password=password)

        def master_for(self, name: str, db: int, decode_responses: bool) -> _FakeRedis:
            seen.update(master=name, db=db)
            return fake

    with (
        patch("redis.sentinel.Sentinel", _Sentinel),
        patch("redis.from_url", side_effect=AssertionError("must not parse sentinel://")),
    ):
        assert _guarded(fake, lambda: "ran")() == "ran"

    assert seen == {
        "nodes": [("s1", 26379), ("s2", 26380)],
        "sentinel_kwargs": {"socket_timeout": 1},
        "password": "s3cret",
        "master": "primary",
        "db": 2,
    }
    assert fake.store == {}  # lock released on the master


def test_sentinel_result_backend_gets_the_master_name() -> None:
    """Import the Celery app with Sentinel configured (fresh interpreter)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "from app.scaling.celery_app import celery_app as c;"
        "print(c.conf.result_backend_transport_options['master_name']);"
        "print(c.conf.broker_transport_options['master_name'])"
    )
    env = {
        **os.environ,
        "REDIS_SENTINEL_URLS": "s1:26379,s2:26379",
        "REDIS_SENTINEL_MASTER": "primary",
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert out[-2:] == ["primary", "primary"]
