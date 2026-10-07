"""Fully-autonomous agents: a config change demotes, re-tests and re-promotes.

Owner decision on a05-F095-04. The rollout gate vouches for a fully-autonomous
agent only through a golden-suite run of its CURRENT behaviour config
(``agent_config_hash``). Changing that config used to be refused (409) until an
operator demoted the agent, changed it, re-ran the suite and promoted it again,
and the self-optimizer never applied a winner to such an agent. Now a behaviour
change to a ``fully-autonomous`` agent (PUT /agents/{id}, a self-optimizer
apply or rollback) is accepted and:

1. the agent is demoted to ``bounded-autonomous`` in the same write as the new
   config, with a pending marker in ``agents.autonomy_revalidation`` (audited,
   reason ``config_changed_pending_eval``);
2. a durable run (MEM-53) of its rollout-gate eval suite is started against the
   new config — the run row is enqueued BEFORE the agent write, so a crash
   between the two leaves at worst a run whose config never existed (its tasks
   fail the config check) or a run the stalled-run sweeper re-dispatches;
3. when the run finishes, the post-run hook (:func:`resolve_revalidation`)
   evaluates the gate for the agent's config: passed -> promoted back to
   ``fully-autonomous`` (audited); failed -> stays bounded, the marker records
   why. The beat sweeper calls :func:`reconcile_pending_revalidations` so a hook
   that failed (or a run that ended ``failed``) still resolves the marker.

Promotion is a compare-and-set on (``autonomy_mode = bounded-autonomous``, the
marker's ``token``, ``state = pending``): an operator who changes the agent's
autonomy in the meantime cancels the marker, and a newer config change replaces
it (new token, the older run is failed as superseded), so a stale run never
re-promotes anything.

Marker states: ``pending`` -> ``promoted`` | ``failed`` | ``cancelled``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from app.observability.logging import get_logger

logger = get_logger(__name__)

FULLY_AUTONOMOUS = "fully-autonomous"
BOUNDED_AUTONOMOUS = "bounded-autonomous"
REASON_CONFIG_CHANGED = "config_changed_pending_eval"

PENDING = "pending"
PROMOTED = "promoted"
FAILED = "failed"
CANCELLED = "cancelled"

# The audit "tool" of every autonomy transition this module makes.
AUDIT_TOOL = "agent.autonomy"
SYSTEM_ACTOR = "system:rollout-gate"

# (tenant_id, tenant_plan, run_id) -> start executing the enqueued run.
RunDispatcher = Callable[[str, str, str], Awaitable[None]]


class RevalidationAgentStore(Protocol):
    """The slice of ``app.api.agents.AgentStore`` this module needs."""

    async def get_async(self, agent_id: str, *, tenant_ctx: Any) -> dict[str, Any] | None: ...

    async def transition_revalidation(
        self,
        agent_id: str,
        *,
        token: str,
        data: dict[str, Any],
        tenant_ctx: Any,
    ) -> bool: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def marker_of(agent: Mapping[str, Any] | None) -> dict[str, Any] | None:
    marker = (agent or {}).get("autonomy_revalidation")
    return dict(marker) if isinstance(marker, Mapping) else None


def is_pending(agent: Mapping[str, Any] | None) -> bool:
    """Is *agent* demoted and waiting for its re-validation run?"""
    marker = marker_of(agent)
    return marker is not None and marker.get("state") == PENDING


def resolve_marker(marker: Mapping[str, Any], state: str, **fields: Any) -> dict[str, Any]:
    """The marker moved to a final *state* (keeps the token, run and history)."""
    return {**marker, **fields, "state": state, "resolved_at": _now()}


def _base_marker(
    *, eval_suite_id: str | None, agent_config_hash: str, source: str, actor: str | None
) -> dict[str, Any]:
    return {
        "token": uuid.uuid4().hex,
        "reason": REASON_CONFIG_CHANGED,
        "source": source,
        "actor": actor or None,
        "from_mode": FULLY_AUTONOMOUS,
        "to_mode": BOUNDED_AUTONOMOUS,
        "eval_suite_id": eval_suite_id or None,
        "agent_config_hash": agent_config_hash,
        "run_id": None,
        "demoted_at": _now(),
    }


def _run_concurrency(task_count: int) -> int:
    from app.core.config import get_settings

    configured = int(getattr(get_settings(), "eval_suite_run_concurrency", 4))
    return max(1, min(configured, task_count))


async def enqueue_revalidation_run(
    eval_store: Any,
    *,
    suite_id: str,
    agent_id: str,
    agent_config: Mapping[str, Any],
    tenant_plan: str,
) -> tuple[str | None, str | None]:
    """Enqueue (not dispatch) a run of *suite_id* pinned to *agent_config*.

    Returns ``(run_id, None)``, or ``(None, reason)`` when the suite cannot run
    (missing or empty). A store error propagates: nothing was written yet.
    """
    from app.intelligence.rollout_gate import agent_config_hash

    meta = await eval_store.get_meta(suite_id)
    if meta is None:
        return None, f"Eval suite {suite_id} not found."
    task_count = int(meta.get("task_count") or 0)
    if task_count == 0:
        return None, f"Eval suite {suite_id} has no golden tasks."
    run_id = uuid.uuid4().hex
    await eval_store.start_run(
        suite_id,
        run_id,
        dataset_version=int(meta["dataset_version"]),
        agent_id=agent_id,
        agent_config_hash=agent_config_hash(dict(agent_config)),
        enqueue=True,
        tenant_plan=tenant_plan,
        concurrency=_run_concurrency(task_count),
    )
    return run_id, None


async def begin_revalidation(
    eval_store: Any,
    *,
    agent_id: str,
    proposed: Mapping[str, Any],
    source: str,
    actor: str | None,
    tenant_plan: str,
) -> dict[str, Any]:
    """The marker to write WITH the demotion, its run already enqueued.

    ``proposed`` is the agent record about to be written. When the suite cannot
    run, the marker is ``failed`` straight away (the agent is still demoted: the
    gate no longer vouches for its config) and says why.
    """
    from app.intelligence.rollout_gate import agent_config_hash

    suite_id = str(proposed.get("eval_suite_id") or "") or None
    marker = _base_marker(
        eval_suite_id=suite_id,
        agent_config_hash=agent_config_hash(dict(proposed)),
        source=source,
        actor=actor,
    )
    if suite_id is None:
        return resolve_marker(
            marker, FAILED,
            error="No eval suite is attached to this agent; nothing can re-validate it.",
        )
    run_id, why_not = await enqueue_revalidation_run(
        eval_store, suite_id=suite_id, agent_id=agent_id, agent_config=proposed,
        tenant_plan=tenant_plan,
    )
    if run_id is None:
        return resolve_marker(marker, FAILED, error=why_not)
    return {**marker, "run_id": run_id, "state": PENDING}


async def abandon_run(eval_store: Any, marker: Mapping[str, Any] | None, why: str) -> None:
    """Fail the marker's enqueued run (the agent write failed, or it was superseded)."""
    run_id = (marker or {}).get("run_id")
    if not run_id:
        return
    try:
        await eval_store.fail_run(str(run_id), why)
    except Exception as exc:  # the run's own config check stops it anyway
        logger.warning("revalidation_run_abandon_failed", run_id=run_id, error=str(exc)[:200])


async def start_revalidation(
    *,
    eval_store: Any,
    agent_store: RevalidationAgentStore,
    tenant_ctx: Any,
    agent_id: str,
    marker: dict[str, Any],
    dispatch: RunDispatcher | None,
    tenant_plan: str,
    previous: Mapping[str, Any] | None = None,
    audit_log: Any = None,
    db_factory: Any = None,
) -> dict[str, Any]:
    """After the demotion is written: audit it, supersede an older run, dispatch.

    Returns the marker as it now stands (``failed`` when dispatching failed).
    """
    tenant_id = str(tenant_ctx.tenant_id)
    if (
        previous is not None
        and previous.get("state") == PENDING
        and previous.get("run_id")
        and previous.get("run_id") != marker.get("run_id")
    ):
        await abandon_run(eval_store, previous, "superseded by a newer configuration change")
    await audit_transition(
        tenant_id=tenant_id, agent_id=agent_id, outcome="demoted", actor=marker.get("actor"),
        note=(
            f"fully-autonomous -> bounded-autonomous; reason={REASON_CONFIG_CHANGED}; "
            f"source={marker.get('source')}; eval_suite={marker.get('eval_suite_id')}; "
            f"run={marker.get('run_id')}"
            + (f"; not started: {marker.get('error')}" if marker.get("state") == FAILED else "")
        ),
        audit_log=audit_log, db_factory=db_factory,
    )
    if marker.get("state") != PENDING:
        return marker
    try:
        if dispatch is None:
            raise RuntimeError("no eval run executor is available")
        await dispatch(tenant_id, tenant_plan, str(marker["run_id"]))
    except Exception as exc:
        error = f"could not start the eval run: {str(exc)[:300]}"
        logger.error("revalidation_dispatch_failed", agent_id=agent_id, error=error)
        await abandon_run(eval_store, marker, error)
        failed = resolve_marker(marker, FAILED, error=error)
        if await agent_store.transition_revalidation(
            agent_id, token=str(marker["token"]), data={"autonomy_revalidation": failed},
            tenant_ctx=tenant_ctx,
        ):
            await audit_transition(
                tenant_id=tenant_id, agent_id=agent_id, outcome="revalidation_failed",
                actor=SYSTEM_ACTOR, note=f"run={marker.get('run_id')}; {error}",
                audit_log=audit_log, db_factory=db_factory,
            )
            return failed
    return marker


def cancelled_by_operator(
    marker: Mapping[str, Any], *, new_mode: str, actor: str | None
) -> dict[str, Any]:
    return resolve_marker(
        marker, CANCELLED, cancelled_reason="autonomy_changed_manually",
        cancelled_by=actor or None, cancelled_to_mode=new_mode,
    )


async def celery_dispatch(tenant_id: str, tenant_plan: str, run_id: str) -> None:
    """Hand an enqueued run to the Celery eval-suite workers."""
    from app.scaling.tasks import run_eval_suite_worker

    run_eval_suite_worker.apply_async(
        args=[tenant_id, tenant_plan, run_id, 0], queue="maintenance"
    )


async def audit_transition(
    *,
    tenant_id: str,
    agent_id: str,
    outcome: str,
    note: str,
    actor: str | None = None,
    audit_log: Any = None,
    db_factory: Any = None,
) -> None:
    """Durable audit row for an autonomy transition (best effort: the change stands).

    The marker itself records every transition too, so a lost audit row never
    loses the history.
    """
    from app.governance.audit import AuditEvent, AuditLog
    from app.governance.permissions import ActionLevel
    from app.tenancy.context import PlanTier, TenantContext

    principal = (actor or SYSTEM_ACTOR)[:64]
    event = AuditEvent(
        goal_id="",
        tool_name=AUDIT_TOOL,
        action_level=ActionLevel.ALLOW_LOG,
        outcome=outcome,
        step_id=agent_id[:64],
        approver=principal,
        note=f"agent {agent_id}: {note}"[:1000],
        api_key_id=principal,
        auth_type="autonomy_revalidation",
    )
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id=principal)
    sink = audit_log
    if sink is None and db_factory is not None:
        sink = AuditLog(db_session_factory=db_factory)
    logger.info("agent_autonomy_transition", tenant_id=tenant_id, agent_id=agent_id,
                outcome=outcome, note=note[:300])
    if sink is None:
        return
    try:
        await sink.record_async(event, tenant_ctx=ctx)
    except Exception as exc:
        logger.error("agent_autonomy_audit_failed", tenant_id=tenant_id, agent_id=agent_id,
                     outcome=outcome, error=f"{type(exc).__name__}: {str(exc)[:200]}")


async def resolve_revalidation(
    *,
    agent_store: RevalidationAgentStore,
    eval_store: Any,
    tenant_ctx: Any,
    run: Mapping[str, Any],
    audit_log: Any = None,
) -> str | None:
    """Resolve the pending marker that *run* belongs to (idempotent).

    Returns ``promoted`` / ``failed``, or ``None`` when the run is not (or no
    longer) the agent's pending re-validation, or it is still running.
    """
    from app.intelligence.rollout_gate import check_agent_rollout_gate

    agent_id = run.get("agent_id")
    run_id = run.get("run_id")
    status = str(run.get("status") or "")
    if not agent_id or not run_id or status not in {"completed", "failed"}:
        return None
    agent = await agent_store.get_async(str(agent_id), tenant_ctx=tenant_ctx)
    marker = marker_of(agent)
    if agent is None or marker is None or marker.get("state") != PENDING:
        return None
    if marker.get("run_id") != run_id:
        return None
    tenant_id = str(tenant_ctx.tenant_id)
    db_factory = getattr(eval_store, "_db", None)
    report: dict[str, Any] = {}
    if status == "completed":
        report = await check_agent_rollout_gate(
            agent_id=str(agent_id),
            eval_suite_id=marker.get("eval_suite_id"),
            tenant_id=tenant_id,
            db=db_factory,
            agent_config=agent,
        )
        passed = bool(report.get("gate_passed"))
        reason = str(report.get("reason") or "")
    else:
        passed = False
        reason = f"the eval run failed: {run.get('error') or 'no detail'}"
    outcome = {
        "pass_rate": report.get("pass_rate"),
        "total_tasks": report.get("total_tasks"),
        "min_pass_rate_required": report.get("min_pass_rate_required"),
        "gate_reason": reason,
    }
    if passed:
        resolved = resolve_marker(marker, PROMOTED, **outcome)
        data: dict[str, Any] = {
            "autonomy_mode": FULLY_AUTONOMOUS,
            "autonomy_revalidation": resolved,
        }
        audit_outcome, result = "promoted", PROMOTED
    else:
        resolved = resolve_marker(marker, FAILED, error=reason, **outcome)
        data = {"autonomy_revalidation": resolved}
        audit_outcome, result = "revalidation_failed", FAILED
    if not await agent_store.transition_revalidation(
        str(agent_id), token=str(marker["token"]), data=data, tenant_ctx=tenant_ctx
    ):
        logger.info("revalidation_superseded", agent_id=agent_id, run_id=run_id)
        return None
    await audit_transition(
        tenant_id=tenant_id, agent_id=str(agent_id), outcome=audit_outcome, actor=SYSTEM_ACTOR,
        note=(
            ("bounded-autonomous -> fully-autonomous; " if passed else "stays bounded-autonomous; ")
            + f"eval_suite={marker.get('eval_suite_id')}; run={run_id}; {reason}"
        ),
        audit_log=audit_log, db_factory=db_factory,
    )
    return result


async def tenant_plan_of(db_factory: Any, tenant_id: str) -> str:
    """The tenant's plan tier (queue routing of the golden goals); ``free`` if unknown."""
    if db_factory is None:
        return "free"
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db_factory() as session, sqlalchemy_rls_context(session, tenant_id):
            row = (
                await session.execute(
                    text("SELECT plan_tier FROM tenants WHERE id = :tid"), {"tid": tenant_id}
                )
            ).first()
        return str(row[0]) if row and row[0] else "free"
    except Exception:
        return "free"


async def reconcile_pending_revalidations(
    *, system_db: Any, app_db: Any, limit: int = 100
) -> dict[str, int]:
    """Resolve pending markers whose run already ended (a lost hook, a failed run).

    The cross-tenant scan runs on the maintenance (BYPASSRLS) session; each
    resolution runs tenant-scoped on the app role, through the same
    compare-and-set as the post-run hook, so racing the hook is harmless.
    """
    from sqlalchemy import text

    from app.api.agents import AgentStore
    from app.db.rls import system_session
    from app.intelligence.eval_suite_store import EvalSuiteStore
    from app.tenancy.context import PlanTier, TenantContext

    async with system_db() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text(
                    "SELECT a.tenant_id, r.id, a.id, r.status, r.error "
                    "FROM agents a JOIN eval_suite_results r "
                    "  ON r.tenant_id = a.tenant_id "
                    " AND r.id = (a.autonomy_revalidation ->> 'run_id') "
                    "WHERE a.is_active = TRUE "
                    "  AND (a.autonomy_revalidation ->> 'state') = 'pending' "
                    "  AND r.status IN ('completed', 'failed') "
                    "ORDER BY r.finished_at NULLS FIRST LIMIT :k"
                ),
                {"k": int(limit)},
            )
        ).all()
    resolved = 0
    for tenant_id, run_id, agent_id, status, error in rows:
        ctx = TenantContext(
            tenant_id=str(tenant_id), plan=PlanTier.FREE, api_key_id="autonomy-revalidation"
        )
        try:
            outcome = await resolve_revalidation(
                agent_store=AgentStore(app_db),
                eval_store=EvalSuiteStore(app_db, str(tenant_id)),
                tenant_ctx=ctx,
                run={"run_id": str(run_id), "agent_id": str(agent_id), "status": str(status),
                     "error": error},
            )
        except Exception as exc:
            logger.error("revalidation_reconcile_failed", tenant_id=tenant_id, agent_id=agent_id,
                         run_id=run_id, error=str(exc)[:200])
            continue
        if outcome is not None:
            resolved += 1
            logger.warning("revalidation_reconciled", tenant_id=tenant_id, agent_id=agent_id,
                           run_id=run_id, outcome=outcome)
    return {"pending_with_finished_run": len(rows), "resolved": resolved}
