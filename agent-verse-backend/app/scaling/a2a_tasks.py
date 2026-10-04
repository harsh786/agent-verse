"""Celery tasks driving inbound A2A tasks to their outcome (A2A-01).

``reconcile_a2a_tasks`` (beat ``reconcile-a2a-tasks``) finalises open A2A tasks
from their goals' persisted status and dispatches due callbacks;
``deliver_a2a_callback`` sends one claimed callback. Every transition is a
conditional UPDATE and every claim uses ``FOR UPDATE SKIP LOCKED``, so any
number of replicas can run them; the beat guard only avoids wasted overlap.
Errors raise: a reconciler that cannot run is a FAILED task, never a quiet
success.
"""

from __future__ import annotations

from typing import Any

from app.scaling.beat_guard import beat_task_guard
from app.scaling.celery_app import celery_app


async def _reconcile_once() -> dict[str, Any]:
    from app.db.session import get_system_session_factory
    from app.services.a2a_tasks import (
        claim_due_callbacks,
        event_result_text,
        finalize_open_tasks,
    )

    system_db = get_system_session_factory()
    outcome = await finalize_open_tasks(system_db, result_text=event_result_text(system_db))
    claimed = await claim_due_callbacks(system_db)
    for task_id, tenant_id in claimed:
        deliver_a2a_callback.delay(task_id, tenant_id)
    return {**outcome, "callbacks_dispatched": [t for t, _ in claimed]}


@celery_app.task(name="app.scaling.a2a_tasks.reconcile_a2a_tasks", bind=True, max_retries=0)
@beat_task_guard(lock_ttl_seconds=120)
def reconcile_a2a_tasks(self: Any) -> dict[str, Any]:
    from app.scaling.tasks import _run_async

    result: dict[str, Any] = _run_async(_reconcile_once())
    return result


async def _deliver(task_id: str, tenant_id: str) -> bool:
    from app.api.a2a import _send_callback
    from app.db.session import get_session_factory
    from app.services.a2a_tasks import deliver_callback

    return await deliver_callback(get_session_factory(), task_id, tenant_id, _send_callback)


@celery_app.task(name="app.scaling.a2a_tasks.deliver_a2a_callback", bind=True, max_retries=0)
def deliver_a2a_callback(self: Any, task_id: str, tenant_id: str) -> dict[str, Any]:
    """Send one claimed A2A callback. A failure is retried by a later claim."""
    from app.scaling.tasks import _run_async

    delivered: bool = _run_async(_deliver(task_id, tenant_id))
    return {"task_id": task_id, "delivered": delivered}


__all__ = ["deliver_a2a_callback", "reconcile_a2a_tasks"]
