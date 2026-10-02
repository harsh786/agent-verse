"""Code sandbox hardening: pids/cap limits, output cap, tenant + audit on the API.

Regressions: containers had no process limit (fork bomb), the default
capability set and no ``no-new-privileges``; output was unbounded; and
POST /tools/execute-code neither passed the tenant nor wrote an audit record.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.tools.code_interpreter as ci


def _fake_docker(logs: bytes = b"ok") -> tuple[ModuleType, MagicMock]:
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = logs
    client = MagicMock()
    client.containers.create.return_value = container
    mod = ModuleType("docker")
    mod.from_env = MagicMock(return_value=client)  # type: ignore[attr-defined]
    return mod, client


@pytest.mark.asyncio
async def test_container_is_created_with_pids_and_capability_limits() -> None:
    mod, client = _fake_docker()
    with patch.dict(sys.modules, {"docker": mod}), patch.object(ci, "_docker_available", lambda: True):
        res = await ci.CodeInterpreter().execute("print(1)", "python", 5)
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["pids_limit"] == ci._PIDS_LIMIT
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kwargs["security_opt"]
    assert kwargs["network_mode"] == "none"
    assert res.exit_code == 0


@pytest.mark.asyncio
async def test_output_is_capped() -> None:
    mod, _client = _fake_docker(logs=b"x" * (ci._MAX_OUTPUT_CHARS + 500))
    with patch.dict(sys.modules, {"docker": mod}), patch.object(ci, "_docker_available", lambda: True):
        res = await ci.CodeInterpreter().execute("print(1)", "python", 5)
    assert len(res.stdout) < ci._MAX_OUTPUT_CHARS + 100
    assert "output truncated: 500 more characters" in res.stdout


def test_api_passes_tenant_and_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.tools import router
    from app.governance.audit import AuditLog
    from app.tenancy.context import PlanTier, TenantContext

    seen: dict[str, Any] = {}

    class _Interp:
        async def execute(self, code: str, language: str, timeout: int, tenant_id: str) -> Any:
            seen["tenant_id"] = tenant_id
            return ci.CodeResult(stdout="hi", stderr="", exit_code=0)

    monkeypatch.setattr("app.tools.code_interpreter.CodeInterpreter", _Interp)
    ctx = TenantContext(tenant_id="tenant-code", plan=PlanTier.FREE, api_key_id="k1")
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    audit = AuditLog()
    app.state.audit_log = audit
    resp = TestClient(app).post("/tools/execute-code", json={"code": "print('hi')"})
    assert resp.status_code == 200
    assert seen["tenant_id"] == "tenant-code"
    events = audit.query(tenant_ctx=ctx)
    assert len(events) == 1
    assert events[0].tool_name == "code_interpreter.python"
    assert "print('hi')" not in events[0].note  # code is hashed, not stored
