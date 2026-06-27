"""Native tool execution endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/tools", tags=["tools"])


class ExecuteCodeRequest(BaseModel):
    code: str
    language: str = "python"  # python | javascript | bash
    timeout: int = 30


class ExecuteCodeResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    success: bool
    timed_out: bool
    execution_time_ms: float


@router.post("/execute-code", response_model=ExecuteCodeResponse)
async def execute_code(
    request: Request, body: ExecuteCodeRequest
) -> ExecuteCodeResponse:
    """Execute code in a sandboxed Docker container.

    Supported languages: python, javascript, bash.
    Maximum timeout: 60 seconds. No network access. No persistent filesystem.
    """
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if body.timeout > 60:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Maximum timeout is 60 seconds",
        )

    from app.tools.code_interpreter import CodeInterpreter

    interpreter = CodeInterpreter()
    result = await interpreter.execute(
        code=body.code,
        language=body.language,
        timeout=body.timeout,
    )
    return ExecuteCodeResponse(**result.to_dict())


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
async def write_file(
    request: Request, path: str, body: FileWriteRequest
) -> dict[str, Any]:
    """Write a file to the tenant's workspace."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id=ctx.tenant_id)
    try:
        bytes_written = await ops.write(path, body.content)
        return {"path": path, "bytes_written": bytes_written, "success": True}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/files/{path:path}", status_code=204)
async def delete_file(request: Request, path: str) -> None:
    """Delete a file from the tenant's workspace."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.file_ops import FileOps

    ops = FileOps(tenant_id=ctx.tenant_id)
    deleted = await ops.delete(path)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"File not found: {path!r}")


# ── Email ─────────────────────────────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    to: str | list[str]
    subject: str
    body: str
    from_addr: str | None = None


@router.post("/email/send")
async def send_email(request: Request, body: SendEmailRequest) -> dict[str, Any]:
    """Send an email via SMTP (uses env-var config; MailHog in dev)."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    from app.tools.email_tool import email_send

    return await email_send(body.to, body.subject, body.body, from_addr=body.from_addr)
