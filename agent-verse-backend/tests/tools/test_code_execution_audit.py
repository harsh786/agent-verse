"""CODE-02: every code execution is durably audited, and fails closed when it can't be.

/tools/execute-code skipped auditing when app.state.audit_log was None, swallowed
audit errors, and AuditLog.record persisted through a fire-and-forget task (a DB
failure lost the record while the call returned 200). The chat execute endpoint
and workflow code steps did not audit at all.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog, AuditPersistenceError
from app.tenancy.context import PlanTier, TenantContext
from app.tools.code_execution import CodeExecutionContext, execute_governed
from app.tools.code_interpreter import CodeResult

CTX = TenantContext(
    tenant_id="tenant-code", plan=PlanTier.FREE, api_key_id="key-1", roles=("operator",)
)


class _Interp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kw: Any) -> CodeResult:
        self.calls.append(kw)
        return CodeResult(stdout="hi\n", stderr="", exit_code=0)


class _FailingDb:
    """A session factory whose commit fails (DB outage)."""

    def __call__(self) -> Any:
        session = MagicMock()
        session.__aenter__ = AsyncMock(side_effect=ConnectionError("db down"))
        session.__aexit__ = AsyncMock(return_value=False)
        return session


async def test_success_records_one_event_with_hash_and_metadata() -> None:
    audit = AuditLog()
    interp = _Interp()
    ctx = CodeExecutionContext(tenant_ctx=CTX, source="chat.execute", ref_id="sess-1")
    res = await execute_governed(
        "print('hi')", "python", 5, ctx=ctx, audit_log=audit, interpreter=interp
    )
    assert res.stdout == "hi\n"
    assert interp.calls[0]["tenant_id"] == "tenant-code"
    events = audit.query(tenant_ctx=CTX)
    assert len(events) == 1
    ev = events[0]
    assert ev.goal_id == "sess-1"
    assert ev.tool_name == "code_interpreter.python"
    assert ev.api_key_id == "key-1"
    assert "source=chat.execute" in ev.note
    assert "sha256=" in ev.note and "bytes=11" in ev.note and "exit_code=0" in ev.note
    assert "print(" not in ev.note  # the code itself is never stored


async def test_db_failure_raises_instead_of_returning_success() -> None:
    audit = AuditLog(db_session_factory=_FailingDb())
    ctx = CodeExecutionContext(tenant_ctx=CTX, source="tools.execute_code")
    with pytest.raises(AuditPersistenceError):
        await execute_governed(
            "print(1)", "python", 5, ctx=ctx, audit_log=audit, interpreter=_Interp()
        )


async def test_no_database_outside_development_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()
    try:
        ctx = CodeExecutionContext(tenant_ctx=CTX, source="tools.execute_code")
        with pytest.raises(AuditPersistenceError):
            await execute_governed(
                "print(1)", "python", 5, ctx=ctx, audit_log=AuditLog(), interpreter=_Interp()
            )
    finally:
        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()


# ── API endpoints ────────────────────────────────────────────────────────────


def _tools_app(audit: Any) -> FastAPI:
    from app.api.tools import router

    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = audit
    return app


def test_execute_code_returns_503_when_the_audit_cannot_be_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.tools.code_execution.CodeInterpreter", lambda **_kw: _Interp())
    client = TestClient(_tools_app(AuditLog(db_session_factory=_FailingDb())))
    resp = client.post("/tools/execute-code", json={"code": "print('hi')"})
    assert resp.status_code == 503
    assert "audit" in resp.json()["detail"].lower()


def test_execute_code_audits_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.tools.code_execution.CodeInterpreter", lambda **_kw: _Interp())
    audit = AuditLog()
    client = TestClient(_tools_app(audit))
    resp = client.post("/tools/execute-code", json={"code": "print('hi')"})
    assert resp.status_code == 200, resp.text
    [ev] = audit.query(tenant_ctx=CTX)
    assert "source=tools.execute_code" in ev.note


def _chat_app(audit: Any) -> FastAPI:
    from app.chat.router import router

    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = audit
    svc = MagicMock()
    svc.aget_session = AsyncMock(return_value={"id": "sess-9"})
    app.state.chat_service = svc
    return app


def test_chat_execute_is_audited_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.tools.code_execution.CodeInterpreter", lambda **_kw: _Interp())
    audit = AuditLog()
    client = TestClient(_chat_app(audit))
    resp = client.post("/chat/sessions/sess-9/execute", json={"code": "print('hi')"})
    assert resp.status_code == 200, resp.text
    [ev] = audit.query(tenant_ctx=CTX)
    assert ev.goal_id == "sess-9" and "source=chat.execute" in ev.note

    failing = TestClient(_chat_app(AuditLog(db_session_factory=_FailingDb())))
    assert (
        failing.post("/chat/sessions/sess-9/execute", json={"code": "print(1)"}).status_code == 503
    )


# ── Workflow code step ──────────────────────────────────────────────────────


async def test_workflow_code_step_runs_through_the_governed_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflow.steps import code_step

    seen: dict[str, Any] = {}

    async def _governed(code: str, language: str, timeout: int, *, ctx: Any, **_: Any) -> Any:
        seen.update(ctx=ctx, language=language)
        return CodeResult(stdout='__AGENTVERSE_STEP_OUTPUT__{"ok": 1}\n', stderr="", exit_code=0)

    monkeypatch.setattr("app.tools.code_execution.execute_governed", _governed)
    out = await code_step._run_in_code_sandbox(
        "s1", "python", "output = {'ok': 1}", {}, tenant_id="t-wf", run_id="run-7"
    )
    assert out == {"ok": 1}
    ctx = seen["ctx"]
    assert ctx.tenant_ctx.tenant_id == "t-wf"
    assert ctx.source == "workflow.code_step"
    assert ctx.ref_id == "run-7" and ctx.step_id == "s1"


async def test_workflow_code_step_without_tenant_fails() -> None:
    from app.workflow.steps import code_step

    with pytest.raises(RuntimeError, match="tenant"):
        await code_step._run_in_code_sandbox(
            "s1", "python", "output = 1", {}, tenant_id="", run_id="r"
        )
