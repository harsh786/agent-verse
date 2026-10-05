"""Celery task running a durable training-data export job (OPS-37)."""

from __future__ import annotations

from typing import Any

from app.scaling.beat_guard import beat_task_guard
from app.scaling.celery_app import celery_app

TASK_NAME = "agentverse.training_export.run"
EXPIRE_TASK_NAME = "agentverse.training_export.expire"
# A busy job (held by a worker with a fresh heartbeat) is retried this many
# times, one stale-heartbeat window apart: enough for a dead worker's claim to
# expire and be taken over; a live worker simply finishes it.
BUSY_RETRIES = 6


@celery_app.task(name=TASK_NAME, bind=True, max_retries=BUSY_RETRIES)  # type: ignore[misc]
def run_training_export(self: Any, job_id: str, tenant_id: str) -> dict[str, Any]:
    """Stream the job's examples into object storage (tenant RLS context).

    Runs on the app role under the job's tenant GUC — never the maintenance
    role: the job belongs to exactly one tenant. Only the ``busy`` outcome is
    retried; any failure is already recorded on the job row and re-raised.
    """
    from app.db.session import get_session_factory, run_in_fresh_loop
    from app.training_export import jobs

    async def _run() -> dict[str, Any]:
        return await jobs.run_export_job(
            get_session_factory(), jobs.object_store_from_env(), job_id, tenant_id
        )

    result: dict[str, Any] = run_in_fresh_loop(_run())
    if result.get("status") == "busy" and self.request.retries < self.max_retries:
        # Redelivered while the claim's heartbeat is fresh: the original worker
        # is alive (it finishes the job) or just died (its claim expires first).
        raise self.retry(countdown=jobs.STALE_HEARTBEAT_SECONDS + 30)
    return result


@celery_app.task(name=EXPIRE_TASK_NAME, bind=True, max_retries=0)  # type: ignore[misc]
@beat_task_guard(lock_ttl_seconds=1800)
def expire_training_exports(self: Any) -> dict[str, Any]:
    """NF-17 beat sweep: delete expired export files, mark their jobs ``expired``.

    Cross-tenant system work on the maintenance (BYPASSRLS) role, in bounded
    batches (Settings.training_export_expiry_*). Fails loudly — an error result,
    never a silent "0 expired" — when the store or the database is unusable.
    """
    from app.core.config import get_settings
    from app.db.session import get_system_session_factory, run_in_fresh_loop
    from app.training_export import jobs

    settings = get_settings()

    async def _run() -> dict[str, Any]:
        return await jobs.expire_finished_exports(
            get_system_session_factory(),
            jobs.object_store_from_env(),
            retention_hours=int(settings.training_export_retention_hours),
            batch_size=int(settings.training_export_expiry_batch_size),
            max_batches=int(settings.training_export_expiry_max_batches),
        )

    try:
        result: dict[str, Any] = run_in_fresh_loop(_run())
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("training_export_expiry_failed: %s", exc)
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return result
