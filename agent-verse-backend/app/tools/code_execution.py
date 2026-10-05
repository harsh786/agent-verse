"""The one governed entrypoint for running tenant code in the sandbox.

Every live caller (POST /tools/execute-code, POST /chat/sessions/{id}/execute
and workflow ``code`` steps) runs code through :func:`execute_governed`, which

* caps concurrency: at most ``code_exec_max_concurrent_per_tenant`` executions
  per tenant across every replica and worker (Redis lease set) and
  ``code_exec_max_concurrent_per_host`` per process; a full cap raises
  :class:`CodeExecutionBusyError` (the API answers 429),
* runs it in :class:`app.tools.code_interpreter.CodeInterpreter` (Docker
  sandbox on a dedicated bounded thread pool, fail-closed without Docker
  outside the dev opt-in), and
* writes exactly one durable audit row (sha256, bytes, language, exit code,
  caller, api key, session/run reference) committed to Postgres under the
  tenant's RLS context before the result is returned. If that row cannot be
  committed the call raises :class:`AuditPersistenceError` (the API answers
  503) instead of returning an unaudited success.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.governance.audit import AuditEvent, AuditLog, AuditPersistenceError
from app.observability.logging import get_logger
from app.reliability.bulkhead import LocalSlotCounter, RedisLeaseLimiter
from app.tenancy.context import TenantContext
from app.tools.code_interpreter import CodeInterpreter, CodeResult

__all__ = [
    "AuditPersistenceError",
    "CodeExecutionBusyError",
    "CodeExecutionContext",
    "execute_governed",
]

_log = get_logger(__name__)

_default_audit_log: AuditLog | None = None


@dataclass(frozen=True)
class CodeExecutionContext:
    """Who runs the code, from where, for what."""

    tenant_ctx: TenantContext
    # "tools.execute_code" | "chat.execute" | "workflow.code_step"
    source: str
    # session id (chat), run id (workflow); stored as the audit row's goal_id.
    ref_id: str = ""
    # workflow step id; stored as the audit row's step_id.
    step_id: str = ""


@contextlib.asynccontextmanager
async def env_redis() -> AsyncIterator[Any]:
    """A short-lived Redis client from REDIS_URL (sentinel/cluster aware), or None.

    For callers without an app-level client (Celery workers run each task in a
    fresh event loop, so a process-wide async client cannot be shared).
    """
    import os

    if not any(
        os.getenv(n) for n in ("REDIS_URL", "REDIS_SENTINEL_URLS", "REDIS_CLUSTER_NODES")
    ):
        yield None
        return
    from app.net.redis_factory import get_redis_kwargs, make_async_redis

    client = make_async_redis(**get_redis_kwargs(), socket_timeout=5)
    try:
        yield client
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


def durable_audit_log(app_log: AuditLog | None = None) -> AuditLog:
    """*app_log* (the app's audit log) or a process DB-backed AuditLog for callers
    without one (e.g. Celery workers)."""
    global _default_audit_log
    if app_log is not None:
        return app_log
    if _default_audit_log is None:
        from app.db.session import get_session_factory

        _default_audit_log = AuditLog(db_session_factory=get_session_factory())
    return _default_audit_log


class CodeExecutionBusyError(RuntimeError):
    """Too many concurrent sandbox executions (the API answers 429)."""

    def __init__(self, scope: str, limit: int) -> None:
        super().__init__(f"too many concurrent code executions ({scope} limit {limit})")
        self.scope = scope
        self.limit = limit


# Per-process caps (each running container holds a sandbox-pool thread).
_local_slots = LocalSlotCounter()
_HOST_KEY = "__host__"
# Seconds a tenant lease outlives the execution deadline (container start/teardown).
_LEASE_MARGIN_S = 60.0


@contextlib.asynccontextmanager
async def _concurrency_slot(tenant_id: str, timeout: int, redis: Any) -> AsyncIterator[None]:
    """Hold one host slot and one of the tenant's slots for the execution.

    The tenant cap is global (Redis lease set shared by every replica and
    worker); while Redis is unreachable it degrades to this process's
    per-tenant count, still bounded. Never blocks: a full cap raises
    :class:`CodeExecutionBusyError`.
    """
    from app.core.config import get_settings

    settings = get_settings()
    host_limit = max(1, int(settings.code_exec_max_concurrent_per_host))
    tenant_limit = max(1, int(settings.code_exec_max_concurrent_per_tenant))
    if not _local_slots.try_acquire(_HOST_KEY, host_limit):
        raise CodeExecutionBusyError("host", host_limit)
    lease_key = f"code_exec:leases:{tenant_id}"
    lease_id = uuid.uuid4().hex
    limiter = RedisLeaseLimiter(redis) if redis is not None else None
    local_tenant_key: str | None = None
    try:
        acquired_remote = False
        if limiter is not None:
            try:
                if not await limiter.try_acquire(
                    lease_key, lease_id, limit=tenant_limit, lease_s=timeout + _LEASE_MARGIN_S
                ):
                    raise CodeExecutionBusyError("tenant", tenant_limit)
                acquired_remote = True
            except CodeExecutionBusyError:
                raise
            except Exception as exc:
                _log.warning("code_exec_tenant_lease_unavailable", error=str(exc)[:200])
        if not acquired_remote:
            local_tenant_key = f"tenant:{tenant_id}"
            if not _local_slots.try_acquire(local_tenant_key, tenant_limit):
                local_tenant_key = None
                raise CodeExecutionBusyError("tenant", tenant_limit)
        try:
            yield
        finally:
            if acquired_remote and limiter is not None:
                with contextlib.suppress(Exception):
                    await limiter.release(lease_key, lease_id)
            if local_tenant_key is not None:
                _local_slots.release(local_tenant_key)
    finally:
        _local_slots.release(_HOST_KEY)


async def execute_governed(
    code: str,
    language: str,
    timeout: int,
    *,
    ctx: CodeExecutionContext,
    audit_log: AuditLog | None = None,
    interpreter: Any = None,
    redis: Any = None,
) -> CodeResult:
    """Run *code* in the sandbox and durably audit it; see the module docstring.

    Raises :class:`CodeExecutionBusyError` when the tenant's (global) or this
    host's concurrent-execution cap is full.
    """
    interp = interpreter or CodeInterpreter(default_timeout=timeout)
    async with _concurrency_slot(ctx.tenant_ctx.tenant_id, timeout, redis):
        result: CodeResult = await interp.execute(
            code=code, language=language, timeout=timeout, tenant_id=ctx.tenant_ctx.tenant_id
        )
    await _audit(ctx, code, language, result, durable_audit_log(audit_log))
    return result


async def _audit(
    ctx: CodeExecutionContext, code: str, language: str, result: CodeResult, audit_log: AuditLog
) -> None:
    from app.governance.permissions import ActionLevel

    raw = code.encode("utf-8", errors="replace")
    await audit_log.record_durable(
        AuditEvent(
            # Ids are never truncated; AuditLog refuses an overflow loudly (P4-2).
            goal_id=ctx.ref_id or ctx.source,
            step_id=ctx.step_id,
            tool_name=f"code_interpreter.{language}"[:200],
            action_level=ActionLevel.ALLOW_LOG,
            outcome="success" if result.success else ("timeout" if result.timed_out else "failed"),
            api_key_id=(getattr(ctx.tenant_ctx, "api_key_id", None) or None),
            note=(
                f"source={ctx.source} sha256={hashlib.sha256(raw).hexdigest()} "
                f"bytes={len(raw)} language={language} exit_code={result.exit_code} "
                f"timed_out={result.timed_out}"
            ),
        ),
        tenant_ctx=ctx.tenant_ctx,
    )
