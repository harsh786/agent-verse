"""COORD-OUTBOX: the dispatcher is scheduled and registered, and fails closed."""

from __future__ import annotations

from typing import Any

import pytest

import app.coordination.outbox_tasks as outbox_tasks
from app.coordination.outbox_dispatcher import CoordinationOutboxDispatcher
from app.scaling.celery_app import celery_app


def test_outbox_dispatch_is_on_the_beat_schedule_and_registered() -> None:
    entry = celery_app.conf.beat_schedule["dispatch-coordination-outbox"]
    assert entry["task"] == "agentverse.coordination.dispatch_outbox"
    assert "app.coordination.outbox_tasks" in celery_app.conf.include
    assert "agentverse.coordination.dispatch_outbox" in celery_app.tasks


@pytest.mark.asyncio
async def test_no_transport_scans_and_claims_nothing() -> None:
    def explode() -> Any:
        raise AssertionError("no DB access without a transport")

    dispatcher = CoordinationOutboxDispatcher(
        session_factory=explode, system_session_factory=explode, publisher=lambda: None
    )
    assert await dispatcher.dispatch_once() == {
        "status": "skipped",
        "reason": "no_stream_transport",
        "delivered": 0,
    }


def test_task_reports_errors_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> dict[str, Any]:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(outbox_tasks, "dispatch_coordination_outbox_once", boom)
    assert outbox_tasks.dispatch_coordination_outbox() == {
        "status": "error",
        "error": "ConnectionError",
    }
