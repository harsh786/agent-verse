"""Native tool execution endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.tenancy.rbac import require_role

router = APIRouter(prefix="/tools", tags=["tools"])


class ExecuteCodeRequest(BaseModel):
    # Bounded like POST /chat/sessions/{id}/execute: an unbounded body was parsed
    # whole in API memory and then streamed to the container.
    code: str = Field(..., min_length=1, max_length=50_000)
    language: Literal["python", "javascript", "bash"] = "python"
    timeout: int = 30


class ExecuteCodeResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    success: bool
    timed_out: bool
    execution_time_ms: float


@router.post("/execute-code", response_model=ExecuteCodeResponse)
async def execute_code(request: Request, body: ExecuteCodeRequest) -> ExecuteCodeResponse:
    """Execute code in a sandboxed Docker container.

    Supported languages: python, javascript, bash.
    Maximum timeout: 60 seconds. No network access. No persistent filesystem.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if body.timeout < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Minimum timeout is 1 second",
        )
    if body.timeout > 60:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Maximum timeout is 60 seconds",
        )

    from app.tools.code_execution import (
        AuditPersistenceError,
        CodeExecutionBusyError,
        CodeExecutionContext,
        execute_governed,
    )

    try:
        result = await execute_governed(
            body.code,
            body.language,
            body.timeout,
            ctx=CodeExecutionContext(tenant_ctx=tenant_ctx, source="tools.execute_code"),
            audit_log=getattr(request.app.state, "audit_log", None),
            redis=getattr(request.app.state, "_redis", None),
        )
    except CodeExecutionBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many concurrent code executions ({exc.scope} limit {exc.limit}).",
            headers={"Retry-After": "5"},
        ) from exc
    except AuditPersistenceError as exc:
        # Never an unaudited 200 for arbitrary code execution.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Code execution could not be audited; result withheld.",
        ) from exc
    return ExecuteCodeResponse(**result.to_dict())


# ── Audit for side-effecting native tools ─────────────────────────────────────


async def _audit_native(
    request: Request,
    ctx: Any,
    *,
    tool: str,
    outcome: str,
    note: str,
    durable: bool = True,
) -> None:
    """Record one audit row for a native-tool side effect.

    ``durable=True`` is the precondition row written BEFORE the side effect:
    if it cannot be committed the request is refused with 503 (nothing is sent,
    written or deleted unaudited). ``durable=False`` is the follow-up outcome
    row for a failed operation (best effort; failure is logged).
    """
    from app.governance.audit import AuditEvent, AuditWriteError
    from app.governance.permissions import ActionLevel
    from app.tools.code_execution import durable_audit_log

    audit = durable_audit_log(getattr(request.app.state, "audit_log", None))
    event = AuditEvent(
        goal_id=f"tools.{tool}"[:64],
        tool_name=tool,
        action_level=ActionLevel.ALLOW_LOG,
        outcome=outcome,
        api_key_id=(getattr(ctx, "api_key_id", None) or None),
        note=note[:2000],
    )
    if not durable:
        try:
            await audit.record_async(event, tenant_ctx=ctx)
        except AuditWriteError as exc:
            import logging

            logging.getLogger(__name__).error("native_tool_outcome_audit_failed: %s", exc)
        return
    try:
        await audit.record_durable(event, tenant_ctx=ctx)
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{tool} could not be audited; nothing was done.",
        ) from exc


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


# ── File Operations ───────────────────────────────────────────────────────────


class FileWriteRequest(BaseModel):
    content: str = ""


@router.get("/files")
async def list_files(request: Request, directory: str = ".") -> list[dict[str, Any]]:
    """List files in the tenant's workspace directory."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id=ctx.tenant_id)
    return await ops.list(directory)


@router.get("/files/{path:path}")
async def read_file(request: Request, path: str) -> dict[str, Any]:
    """Read a file from the tenant's workspace."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id=ctx.tenant_id)
    try:
        content = await ops.read(path)
        return {"path": path, "content": content, "success": True}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/files/{path:path}", status_code=201)
async def write_file(request: Request, path: str, body: FileWriteRequest) -> dict[str, Any]:
    """Write a file to the tenant's workspace."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id=ctx.tenant_id)
    size = len(body.content.encode("utf-8"))
    await _audit_native(
        request,
        ctx,
        tool="workspace.write",
        outcome="requested",
        note=f"path={path} bytes={size} sha256={_sha256(body.content)}",
    )
    try:
        bytes_written = await ops.write(path, body.content)
        return {"path": path, "bytes_written": bytes_written, "success": True}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        await _audit_native(
            request,
            ctx,
            tool="workspace.write",
            outcome="failed",
            note=f"path={path} error={type(exc).__name__}",
            durable=False,
        )
        raise HTTPException(status_code=500, detail="Workspace write failed") from exc


@router.delete("/files/{path:path}", status_code=204)
async def delete_file(request: Request, path: str) -> None:
    """Delete a file from the tenant's workspace."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id=ctx.tenant_id)
    await _audit_native(
        request, ctx, tool="workspace.delete", outcome="requested", note=f"path={path}"
    )
    deleted = await ops.delete(path)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"File not found: {path!r}")


# ── Email ─────────────────────────────────────────────────────────────────────


class SendEmailRequest(BaseModel):
    to: str | list[str]
    subject: str
    body: str
    # Only the platform-verified sender is accepted here (anything else → 400);
    # use ``reply_to`` to direct replies to a tenant address.
    from_addr: str | None = None
    reply_to: str | None = None


@router.post("/email/send")
async def send_email(
    request: Request,
    body: SendEmailRequest,
    _rbac: None = Depends(require_role("operator", "admin")),
) -> dict[str, Any]:
    """Send an email via SMTP (uses env-var config; MailHog in dev).

    The ``From`` header is always the platform-verified sender: this relay uses
    platform SMTP credentials, so a tenant-chosen From was sender spoofing. For
    the same reason it is bounded: operator/admin keys only, at most
    ``email_max_recipients`` per message (422) and a per-tenant daily recipient
    quota shared by every replica (429; 503 when the quota store is down).
    """
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.core.config import get_settings
    from app.tools import email_quota, email_tool

    recipients = [body.to] if isinstance(body.to, str) else list(body.to)
    max_recipients = max(1, int(get_settings().email_max_recipients))
    if len(recipients) > max_recipients:
        raise HTTPException(
            status_code=422,
            detail=f"At most {max_recipients} recipients per message.",
        )
    try:
        quota = await email_quota.consume(
            getattr(request.app.state, "_redis", None), ctx.tenant_id, ctx.plan, len(recipients)
        )
    except email_quota.EmailQuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily email recipient quota exhausted ({exc.used}/{exc.limit}).",
            headers={"Retry-After": "3600"},
        ) from exc
    except email_quota.EmailQuotaUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email quota store unavailable; nothing was sent.",
        ) from exc
    recipients_digest = _sha256(",".join(sorted(r.strip().lower() for r in recipients)))
    await _audit_native(
        request,
        ctx,
        tool="email.send",
        outcome="requested",
        note=(
            f"recipients={len(recipients)} recipients_sha256={recipients_digest} "
            f"subject_sha256={_sha256(body.subject)} body_bytes={len(body.body.encode())}"
        ),
    )
    result = await email_tool.email_send(
        body.to,
        body.subject,
        body.body,
        from_addr=body.from_addr,
        reply_to=body.reply_to,
        tenant_id=str(ctx.tenant_id),
    )
    if not result.get("success", True):
        await _audit_native(
            request,
            ctx,
            tool="email.send",
            outcome="rejected" if result.get("rejected") else "failed",
            note=f"recipients_sha256={recipients_digest}",
            durable=False,
        )
    if result.get("rejected"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("error"))
    if not result.get("success", True):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "Email send failed"),
        )
    return {**result, "quota_remaining": quota.remaining, "quota_limit": quota.limit}
