"""Integration (TRG-54): on a real Redis, N concurrent beat replicas enqueue a
due polling trigger exactly once per interval.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/scaling/test_poll_claim_redis_integration.py -m integration
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import pytest

from app.scaling import tasks

pytestmark = pytest.mark.integration


def test_concurrent_beats_enqueue_each_poll_once(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis as sync_redis

    sent: list[str] = []
    lock = threading.Lock()

    def _apply_async(*, kwargs: dict[str, Any], queue: str) -> None:
        with lock:
            sent.append(f"{queue}:{kwargs['key']}")

    monkeypatch.setattr(tasks.poll_trigger, "apply_async", _apply_async)
    now = dt.datetime(2026, 10, 2, 12, 0)
    scheds = {
        f"schedule:t1:p{i}": {
            "schedule_id": f"p{i}",
            "tenant_id": "t1",
            "trigger_type": "rss_feed" if i % 2 else "api_poll",
            "poll_interval_seconds": 120,
        }
        for i in range(20)
    }
    barrier = threading.Barrier(8)

    def _beat() -> None:
        r = sync_redis.from_url(redis_url, decode_responses=True)
        barrier.wait()
        for key, sched in scheds.items():
            tasks._enqueue_poll_trigger(key, sched, r, now)

    threads = [threading.Thread(target=_beat) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(sent) == sorted(f"triggers.poll:{k}" for k in scheds)
