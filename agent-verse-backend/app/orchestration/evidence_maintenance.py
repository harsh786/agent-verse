"""Beat task: purge expired strategy certification evidence (CORE-17, CORE-36).

Evidence rows carry an ``expires_at`` (30 days by default) but nothing ever
deleted them, so ``strategy_certification_evidence`` grew forever (CORE-17).
One row is written per strategy per finished goal, so a fixed daily batch cap
fell behind production write volume and the table still grew (CORE-36). The
purge now runs hourly, deletes until the backlog is drained (time-boxed, and
re-enqueues itself when the time box ends first), and raises on failure so
Celery retries and the failure is visible.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

_log = get_logger(__name__)

PURGE_BATCH_SIZE = 5000
# Per run; a run that hits it re-enqueues itself, so the backlog keeps draining.
PURGE_TIME_BUDGET_S = 240.0


async def purge_expired_strategy_evidence_once(
    db_factory: Any,
    *,
    batch_size: int = PURGE_BATCH_SIZE,
    time_budget_s: float = PURGE_TIME_BUDGET_S,
) -> dict[str, Any]:
    from app.orchestration.strategy_certification import StrategyEvidenceStore

    deleted, drained = await StrategyEvidenceStore(db_factory).purge_until_drained(
        batch_size=batch_size, time_budget_s=time_budget_s
    )
    return {"deleted": deleted, "drained": drained}


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="agentverse.maintenance.purge_expired_strategy_evidence",
    max_retries=3,
)
def purge_expired_strategy_evidence(self: Any) -> dict[str, Any]:
    """Delete expired evidence in batches on the maintenance (BYPASSRLS) role."""

    from app.db.session import get_system_session_factory, run_in_fresh_loop

    try:
        out = run_in_fresh_loop(
            purge_expired_strategy_evidence_once(get_system_session_factory())
        )
    except Exception as exc:
        _log.warning("strategy_evidence_purge_failed", error=type(exc).__name__)
        # Retried, then the task FAILS (visible, alertable) — never a quiet
        # "error" result while the table keeps growing.
        raise self.retry(exc=exc, countdown=60) from exc
    _log.info("strategy_evidence_purged", deleted=out["deleted"], drained=out["drained"])
    if not out["drained"]:
        # Backlog left after the time box: continue right away in a fresh run.
        purge_expired_strategy_evidence.apply_async(queue="maintenance", countdown=1)
    return {"status": "ok", **out}
