"""Celery task running a durable training-data export job (OPS-37)."""

from __future__ import annotations

from typing import Any

from app.scaling.celery_app import celery_app

TASK_NAME = "agentverse.training_export.run"


@celery_app.task(name=TASK_NAME, bind=True, max_retries=0)  # type: ignore[misc]
def run_training_export(self: Any, job_id: str, tenant_id: str) -> dict[str, Any]:
    """Stream the job's examples into object storage (tenant RLS context).

    Runs on the app role under the job's tenant GUC — never the maintenance
    role: the job belongs to exactly one tenant.
    """
    from app.db.session import get_session_factory, run_in_fresh_loop
    from app.training_export.jobs import object_store_from_env, run_export_job

    async def _run() -> dict[str, Any]:
        return await run_export_job(
            get_session_factory(), object_store_from_env(), job_id, tenant_id
        )

    result: dict[str, Any] = run_in_fresh_loop(_run())
    return result
