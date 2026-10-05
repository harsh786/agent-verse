"""SVC-05: a worker-run goal's live events carry their durable sequence.

run_goal's append_submitted_goal_event published to Redis BEFORE persisting, so
the live event had no ``_seq`` and the SSE id fell back to a per-connection
counter. It now persists first and publishes the sequence the store assigned.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.scaling.test_worker_event_sanitization import _DeniedGraph, _Redis


def _run(monkeypatch: pytest.MonkeyPatch, append: Any) -> list[dict[str, Any]]:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks
    from app.services.event_store import EventStore
    from app.services.goal_service import GoalService

    redis = _Redis()

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(_graph_mod, "AgentGraph", _DeniedGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: redis)
    monkeypatch.setattr(tasks, "_finalize_owning_mission", _noop)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _noop)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _noop)
    monkeypatch.setattr(EventStore, "append_event", append)

    tasks.run_goal.push_request(retries=0, called_directly=True)
    try:
        tasks.run_goal.run("goal-seq-1", "tenant-1", "do the thing")
    finally:
        tasks.run_goal.pop_request()
    return [json.loads(d) for ch, d in redis.published if ch == "goal_events:tenant-1:goal-seq-1"]


def test_worker_events_are_published_with_their_store_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[dict[str, Any]] = []

    async def _append(self: Any, goal_id: str, event: dict[str, Any], *, tenant_ctx: Any) -> int:
        stored.append(dict(event))
        return 40 + len(stored)

    published = _run(monkeypatch, _append)

    assert published, "the worker published no goal events"
    assert [p["_seq"] for p in published] == [41 + i for i in range(len(published))]
    assert [p["type"] for p in published] == [e["type"] for e in stored]
    assert all("_seq" not in e for e in stored)  # the stored payload stays unchanged


def test_unstored_worker_event_is_still_published_without_a_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _append(self: Any, *a: Any, **k: Any) -> int:
        raise RuntimeError("db down")

    published = _run(monkeypatch, _append)

    assert published  # Redis delivery never depends on the DB
    assert all("_seq" not in p for p in published)
