"""A2A-01: the reconcile beat is registered and dispatches every claimed callback."""

from __future__ import annotations

from typing import Any

import pytest

import app.scaling.a2a_tasks as a2a_tasks
from app.scaling.celery_app import celery_app


def test_reconcile_beat_targets_a_registered_maintenance_task() -> None:
    entry = celery_app.conf.beat_schedule["reconcile-a2a-tasks"]
    assert entry["task"] == "app.scaling.a2a_tasks.reconcile_a2a_tasks"
    assert "app.scaling.a2a_tasks" in celery_app.conf.include
    assert entry["task"] in celery_app.tasks
    assert "app.scaling.a2a_tasks.deliver_a2a_callback" in celery_app.tasks
    routes = celery_app.conf.task_routes
    assert routes[entry["task"]] == {"queue": "maintenance"}
    assert routes["app.scaling.a2a_tasks.deliver_a2a_callback"] == {"queue": "maintenance"}


async def test_reconcile_once_finalises_then_dispatches_each_claimed_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db.session as session_mod
    import app.services.a2a_tasks as svc

    factory = object()
    calls: list[str] = []

    async def _finalize(f: Any, *, result_text: Any) -> dict[str, list[str]]:
        assert f is factory
        calls.append("finalize")
        return {"finalized": ["t1"], "errored": []}

    async def _claim(f: Any) -> list[tuple[str, str]]:
        calls.append("claim")
        return [("t1", "ten-a"), ("t2", "ten-b")]

    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(session_mod, "get_system_session_factory", lambda: factory)
    monkeypatch.setattr(svc, "finalize_open_tasks", _finalize)
    monkeypatch.setattr(svc, "claim_due_callbacks", _claim)
    monkeypatch.setattr(
        a2a_tasks.deliver_a2a_callback, "delay", lambda t, ten: dispatched.append((t, ten))
    )

    out = await a2a_tasks._reconcile_once()
    assert calls == ["finalize", "claim"]
    assert dispatched == [("t1", "ten-a"), ("t2", "ten-b")]
    assert out["finalized"] == ["t1"] and out["callbacks_dispatched"] == ["t1", "t2"]


async def test_a_reconcile_failure_raises_not_a_quiet_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db.session as session_mod
    import app.services.a2a_tasks as svc

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(session_mod, "get_system_session_factory", lambda: object())
    monkeypatch.setattr(svc, "finalize_open_tasks", _boom)
    with pytest.raises(RuntimeError, match="db down"):
        await a2a_tasks._reconcile_once()
