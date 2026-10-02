"""The one governed entrypoint for running tenant code in the sandbox.

Every live caller (POST /tools/execute-code, POST /chat/sessions/{id}/execute
and workflow ``code`` steps) runs code through :func:`execute_governed`, which

* runs it in :class:`app.tools.code_interpreter.CodeInterpreter` (Docker
  sandbox, fail-closed without Docker outside the dev opt-in), and
* writes exactly one durable audit row (sha256, bytes, language, exit code,
  caller, api key, session/run reference) committed to Postgres under the
  tenant's RLS context before the result is returned. If that row cannot be
  committed the call raises :class:`AuditPersistenceError` (the API answers
  503) instead of returning an unaudited success.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.governance.audit import AuditEvent, AuditLog, AuditPersistenceError
from app.tenancy.context import TenantContext
from app.tools.code_interpreter import CodeInterpreter, CodeResult

__all__ = ["AuditPersistenceError", "CodeExecutionContext", "execute_governed"]

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


def _process_audit_log() -> AuditLog:
    """A DB-backed AuditLog for callers without one (e.g. Celery workers)."""
    global _default_audit_log
    if _default_audit_log is None:
        from app.db.session import get_session_factory

        _default_audit_log = AuditLog(db_session_factory=get_session_factory())
    return _default_audit_log


async def execute_governed(
    code: str,
    language: str,
    timeout: int,
    *,
    ctx: CodeExecutionContext,
    audit_log: AuditLog | None = None,
    interpreter: Any = None,
) -> CodeResult:
    """Run *code* in the sandbox and durably audit it; see the module docstring."""
    interp = interpreter or CodeInterpreter(default_timeout=timeout)
    result: CodeResult = await interp.execute(
        code=code, language=language, timeout=timeout, tenant_id=ctx.tenant_ctx.tenant_id
    )
    await _audit(ctx, code, language, result, audit_log or _process_audit_log())
    return result


async def _audit(
    ctx: CodeExecutionContext, code: str, language: str, result: CodeResult, audit_log: AuditLog
) -> None:
    from app.governance.permissions import ActionLevel

    raw = code.encode("utf-8", errors="replace")
    await audit_log.record_durable(
        AuditEvent(
            goal_id=(ctx.ref_id or ctx.source)[:64],
            step_id=ctx.step_id[:64],
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
