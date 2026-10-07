"""What happens once a durable eval-suite run completes (run by exactly one worker)."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


async def on_run_completed(
    store: Any,
    run: dict[str, Any],
    tenant_ctx: Any,
    *,
    agent_store: Any = None,
    audit_log: Any = None,
) -> None:
    """Post-run hook of a completed run (called once, by the finalizing worker).

    A run that re-validates a demoted fully-autonomous agent (owner decision on
    a05-F095-04) resolves the agent's pending marker here: promoted back when
    the gate passes, left bounded otherwise. ``agent_store`` defaults to a
    DB-backed ``AgentStore`` over the run store's session factory; the
    beat-driven reconciliation covers a hook that fails.
    """
    logger.info(
        "eval_suite_run_finalized",
        run_id=run.get("run_id"),
        suite_id=run.get("suite_id"),
        agent_id=run.get("agent_id"),
    )
    if not run.get("agent_id"):
        return
    if agent_store is None:
        db = getattr(store, "_db", None)
        if db is None:
            return
        from app.api.agents import AgentStore

        agent_store = AgentStore(db)
    from app.intelligence.autonomy_revalidation import resolve_revalidation

    outcome = await resolve_revalidation(
        agent_store=agent_store, eval_store=store, tenant_ctx=tenant_ctx, run=run,
        audit_log=audit_log,
    )
    if outcome is not None:
        logger.info(
            "agent_revalidation_resolved",
            run_id=run.get("run_id"),
            agent_id=run.get("agent_id"),
            outcome=outcome,
        )
