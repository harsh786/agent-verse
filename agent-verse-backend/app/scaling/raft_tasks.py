"""Celery beat task that advances in-flight RAFT fine-tune jobs.

Before this, a job only moved when someone called ``POST /rag/raft/jobs/{id}/
refresh``: a model that finished training stayed ``submitted`` forever. The beat
entry ``poll-raft-fine-tune-jobs`` (celery_app.py) runs this on the maintenance
queue; each tick scans a bounded, oldest-first batch of submitted/running jobs
across tenants (maintenance session) and refreshes each under its own tenant RLS
context, recording per-job failures on the job (see
``RAFTService.poll_in_flight_jobs``).
"""

from __future__ import annotations

from typing import Any, cast

from app.scaling.celery_app import celery_app


@celery_app.task(
    name="app.scaling.raft_tasks.poll_raft_fine_tune_jobs",
    bind=True,
    max_retries=0,
    queue="maintenance",
)
def poll_raft_fine_tune_jobs(self: Any) -> dict[str, Any]:
    from app.rag.raft_wiring import poll_raft_jobs_once
    from app.scaling.tasks import _run_async

    return cast(dict[str, Any], _run_async(poll_raft_jobs_once()))


__all__ = ["poll_raft_fine_tune_jobs"]
