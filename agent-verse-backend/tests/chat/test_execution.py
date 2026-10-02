"""Tests for inline code execution — 10 cases."""

from __future__ import annotations

import pytest

from app.chat.execution import ChatCodeExecutor
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-chat", plan=PlanTier.FREE, api_key_id="k")


@pytest.fixture()
def executor(monkeypatch: pytest.MonkeyPatch) -> ChatCodeExecutor:
    # No Docker in unit tests: exercise the explicit development-only opt-in.
    import app.tools.code_interpreter as ci

    monkeypatch.setattr(ci, "_docker_available", lambda: False)
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    return ChatCodeExecutor()


async def test_execute_python_hello_world(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "print('hello world')", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.exit_code == 0
    assert "hello" in result.stdout


async def test_execute_python_arithmetic(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "print(2 + 2)", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.exit_code == 0
    assert "4" in result.stdout


async def test_execute_python_syntax_error(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "def broken(:", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.exit_code != 0


async def test_execute_python_runtime_error(executor: ChatCodeExecutor) -> None:
    result = await executor.execute("1/0", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog())
    assert result.exit_code != 0
    assert "ZeroDivisionError" in result.stderr or result.exit_code != 0


async def test_execute_bash_echo(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "echo 'bash works'", "bash", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.exit_code == 0
    assert "bash works" in result.stdout


async def test_execute_unsupported_language_returns_error(executor: ChatCodeExecutor) -> None:
    result = await executor.execute("SELECT 1", "sql", "s1", tenant_ctx=CTX, audit_log=AuditLog())
    assert result.exit_code == 1
    assert result.error == "unsupported_language"


async def test_execute_timeout(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "import time; time.sleep(60)",
        "python",
        "s1",
        timeout=1,
        tenant_ctx=CTX,
        audit_log=AuditLog(),
    )
    assert result.error == "timeout"
    assert result.exit_code == 124


async def test_execute_records_duration(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "print('hi')", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.duration_ms >= 0


async def test_execute_language_alias_py(executor: ChatCodeExecutor) -> None:
    result = await executor.execute(
        "print('alias')", "py", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.exit_code == 0


async def test_execute_stdout_stderr_separation(executor: ChatCodeExecutor) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr)"
    result = await executor.execute(code, "python", "s1", tenant_ctx=CTX, audit_log=AuditLog())
    assert "out" in result.stdout
    assert "err" in result.stderr


async def test_snippets_never_see_the_api_process_secrets(
    executor: ChatCodeExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: snippets ran via subprocess on the API host with its full
    environment (DB URLs, vault master key, provider keys)."""
    monkeypatch.setenv("VAULT_MASTER_KEY", "super-secret-master-key")
    result = await executor.execute(
        "import os; print(os.environ.get('VAULT_MASTER_KEY', 'absent'))",
        "python",
        "s1",
        tenant_ctx=CTX,
        audit_log=AuditLog(),
    )
    assert result.exit_code == 0
    assert "super-secret-master-key" not in result.stdout
    assert "absent" in result.stdout


async def test_without_a_sandbox_nothing_runs_on_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tools.code_interpreter as ci

    monkeypatch.setattr(ci, "_docker_available", lambda: False)
    monkeypatch.delenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", raising=False)
    result = await ChatCodeExecutor().execute(
        "print('ran')", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.exit_code != 0 and "ran" not in result.stdout

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
    result = await ChatCodeExecutor().execute(
        "print('ran')", "python", "s1", tenant_ctx=CTX, audit_log=AuditLog()
    )
    assert result.error == "sandbox_unavailable" and "ran" not in result.stdout


def test_execute_endpoint_requires_the_operator_role() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.chat.router import router
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    viewer = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k", roles=("viewer",))

    async def _resolve(key: str) -> TenantContext | None:
        return viewer if key == "viewer-key" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    r = TestClient(app).post(
        "/chat/sessions/s1/execute",
        json={"code": "print(1)", "language": "python"},
        headers={"X-API-Key": "viewer-key"},
    )
    assert r.status_code == 403
