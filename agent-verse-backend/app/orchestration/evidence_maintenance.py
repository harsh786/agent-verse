"""Beat task: purge expired strategy certification evidence (CORE-17).

Evidence rows carry an ``expires_at`` (30 days by default) but nothing ever
deleted them, so ``strategy_certification_evidence`` grew forever.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

_log = get_logger(__name__)


async def purge_expired_strategy_evidence_once(
    db_factory: Any, *, batch_size: int = 1000, max_batches: int = 100
) -> int:
    from app.orchestration.strategy_certification import StrategyEvidenceStore

    return await StrategyEvidenceStore(db_factory).purge_expired(
        batch_size=batch_size, max_batches=max_batches
    )


@celery_app.task(name="agentverse.maintenance.purge_expired_strategy_evidence")  # type: ignore[untyped-decorator]
def purge_expired_strategy_evidence() -> dict[str, Any]:
    """Delete expired evidence in bounded batches on the maintenance (BYPASSRLS) role."""
    import asyncio

    from app.db.session import get_system_session_factory

    try:
        deleted = asyncio.run(purge_expired_strategy_evidence_once(get_system_session_factory()))
    except Exception as exc:
        _log.warning("strategy_evidence_purge_failed", error=str(exc)[:200])
        return {"status": "error", "error": type(exc).__name__}
    _log.info("strategy_evidence_purged", deleted=deleted)
    return {"status": "ok", "deleted": deleted}
