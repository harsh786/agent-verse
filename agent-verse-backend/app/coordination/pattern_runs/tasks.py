"""Worker task: execute an admitted coordination pattern run (ORG-39).

The REST route used to await the whole pattern (up to 40 LLM calls, a 300 s
deadline) inside the HTTP request: proxies answered 504 while the run continued,
retries got 409, and a restart stranded the run. The route now admits the run
(persists the run document) and enqueues this task with ids only; the task
resumes the run from its checkpoint on a worker. ``task_acks_late`` +
``task_reject_on_worker_lost`` redeliver it if the worker dies mid-run, and every
step resumes from the persisted checkpoint (a terminal run is returned as stored).
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

_log = get_logger(__name__)

TASK_NAME = "agentverse.coordination.run_pattern"


def tenant_payload(tenant_ctx: Any) -> dict[str, Any]:
    """The serialisable identity the worker rebuilds the TenantContext from."""
    plan = getattr(tenant_ctx, "plan", "free")
    return {
        "tenant_id": str(tenant_ctx.tenant_id),
        "plan": str(getattr(plan, "value", plan)),
        "api_key_id": str(getattr(tenant_ctx, "api_key_id", "") or "pattern-run"),
        "roles": list(getattr(tenant_ctx, "roles", ()) or ()),
        "scopes": list(getattr(tenant_ctx, "scopes", ()) or ()),
    }


def _tenant_ctx(payload: dict[str, Any]) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    try:
        plan = PlanTier(payload.get("plan", "free"))
    except ValueError:
        plan = PlanTier.FREE
    return TenantContext(
        tenant_id=str(payload["tenant_id"]),
        plan=plan,
        api_key_id=str(payload.get("api_key_id") or "pattern-run"),
        roles=tuple(payload.get("roles") or ()),
        scopes=tuple(payload.get("scopes") or ()),
    )


def _worker_provider() -> Any:
    """The deployment's real provider, with the same precedence as the API and the
    goal worker; None when only the no-key FakeProvider would answer."""
    from app.providers.fake import FakeProvider
    from app.providers.registry import resolve_provider
    from app.scaling.tasks import _worker_deployment_provider

    provider = _worker_deployment_provider()
    if provider is None:
        resolved = resolve_provider()
        provider = None if isinstance(resolved, FakeProvider) else resolved
    return provider


async def run_pattern_once(
    tenant: dict[str, Any],
    session_id: str,
    pattern: str,
    execution_id: str,
    *,
    state: Any = None,
) -> dict[str, Any]:
    from app.coordination.pattern_runs.service import PatternRunService

    redis_client = None
    if state is None:
        import redis.asyncio as aioredis

        from app.coordination.pattern_runs.goal_bridge import build_pattern_state
        from app.core.config import get_settings
        from app.db.session import get_session_factory

        redis_url = (get_settings().redis_url or "").strip()
        redis_client = aioredis.from_url(redis_url) if redis_url else None
        state = build_pattern_state(
            db_factory=get_session_factory(), provider=_worker_provider(), redis=redis_client
        )
    try:
        return await PatternRunService(state).resume(
            _tenant_ctx(tenant), session_id, pattern, execution_id
        )
    finally:
        if redis_client is not None:
            await redis_client.aclose()


@celery_app.task(name=TASK_NAME)  # type: ignore[untyped-decorator]
def run_pattern(
    tenant: dict[str, Any], session_id: str, pattern: str, execution_id: str
) -> dict[str, Any]:
    """Execute one admitted pattern run; failures raise so Celery records them.

    Runs on ``run_in_fresh_loop`` (L-01): ``asyncio.run`` left pooled asyncpg
    connections on a dead loop and broke the next task on the worker."""
    from app.db import session as db_session

    result = db_session.run_in_fresh_loop(
        run_pattern_once(tenant, session_id, pattern, execution_id)
    )
    _log.info(
        "coordination_pattern_run_finished",
        pattern=pattern,
        execution_id=execution_id,
        phase=result.get("phase"),
    )
    return {
        "execution_id": execution_id,
        "phase": result.get("phase"),
        "terminal_reason": result.get("terminal_reason"),
    }


__all__ = ["TASK_NAME", "run_pattern", "run_pattern_once", "tenant_payload"]
