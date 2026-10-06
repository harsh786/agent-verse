"""Beat task: restore Celery messages left unacked by dead workers (a06-F099-03).

Beat entry ``restore-dead-worker-messages`` (celery_app.py) runs this every 60 s
on the ``maintenance`` queue; ``beat_task_guard`` keeps it single-flight across
replicas. The logic and its safety argument live in
:mod:`app.scaling.dead_worker_restore`.
"""

from __future__ import annotations

from typing import Any

from app.scaling.beat_guard import beat_task_guard
from app.scaling.celery_app import celery_app


@celery_app.task(
    name="app.scaling.dead_worker_tasks.restore_dead_worker_messages",
    bind=True,
    max_retries=0,
    queue="maintenance",
)
@beat_task_guard(lock_ttl_seconds=120)
def restore_dead_worker_messages(self: Any) -> dict[str, Any]:
    from app.scaling import dead_worker_restore as dwr

    enabled, grace = dwr._settings()
    if not enabled:
        return {"skipped": True, "reason": "disabled"}
    with dwr.open_broker_state(celery_app) as state:
        if state is None:
            return {"skipped": True, "reason": "broker_not_redis"}
        report = dwr.restore_dead_worker_messages(state, grace_seconds=grace)
    return report.as_dict()


__all__ = ["restore_dead_worker_messages"]
