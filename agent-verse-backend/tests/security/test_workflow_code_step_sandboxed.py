"""The workflow "code" step must never exec() user code in the API/worker process.

Regression: without an execution_environment the step fell back to
``exec(code, {}, {"__builtins__": {}})`` in-process — escapable, so any
workflow author could run code on the control plane with its env secrets.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tools.code_interpreter import CodeResult
from app.workflow.steps import code_step
from app.workflow.steps.code_step import CodeStepNode

# The step runs through the governed entrypoint (app.tools.code_execution): it
# needs the run's tenant (every execution is audited against one).
_STATE = {"tenant_id": "t-wf", "run_id": "run-1"}

_real_exec = builtins.exec


class _Ctx:
    def resolve(self, value: Any, state: Any) -> Any:
        return value

    def resolve_dict(self, value: Any, state: Any) -> Any:
        return dict(value or {})


class _Step:
    id = "c1"
    runtime = "python"
    code = "output = {'doubled': inputs['n'] * 2}"
    input = {"n": 21}


def _no_exec(source: Any, *a: Any, **k: Any) -> Any:
    # The interpreter itself exec()s plenty (module code objects on import,
    # dataclass-generated __init__ source), so only exec of the STEP'S code —
    # the old in-process fallback ``exec(code, {}, {...})`` — is forbidden.
    text = source.decode() if isinstance(source, bytes) else source
    if isinstance(text, str) and _Step.code in text:
        raise AssertionError("code step must not exec() in-process")
    return _real_exec(source, *a, **k)


@pytest.fixture(autouse=True)
def audit_log(monkeypatch: pytest.MonkeyPatch) -> AuditLog:
    """Code executions are durably audited; unit tests use the in-memory log."""
    log = AuditLog()
    monkeypatch.setattr("app.tools.code_execution._default_audit_log", log)
    return log


def _state() -> Any:
    return dict(_STATE)


async def test_code_step_runs_in_sandbox_not_in_process(
    monkeypatch: pytest.MonkeyPatch, audit_log: AuditLog
) -> None:
    seen: dict[str, Any] = {}

    async def fake_execute(self: Any, code: str, language: str = "python", **_: Any) -> Any:
        seen["code"], seen["language"] = code, language
        return CodeResult(
            stdout=f"noise\n{code_step._OUTPUT_MARKER}" + '{"doubled": 42}\n',
            stderr="",
            exit_code=0,
        )

    monkeypatch.setattr("app.tools.code_interpreter.CodeInterpreter.execute", fake_execute)
    monkeypatch.setattr(builtins, "exec", _no_exec)

    out = await CodeStepNode(_Step(), _Ctx()).execute(_state())  # type: ignore[arg-type]

    assert out["step_outputs"]["c1"] == {"doubled": 42}
    assert seen["language"] == "python"
    assert "inputs['n'] * 2" in seen["code"]
    # ...and the sandboxed execution was audited against the run's tenant.
    tenant = TenantContext(tenant_id="t-wf", plan=PlanTier.FREE, api_key_id="k")
    events = audit_log.query(tenant_ctx=tenant)
    assert [e.tool_name for e in events] == ["code_interpreter.python"]


async def test_code_step_fails_closed_without_sandbox_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("app.tools.code_interpreter._docker_available", lambda: False)
    monkeypatch.setattr(builtins, "exec", _no_exec)

    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        await CodeStepNode(_Step(), _Ctx()).execute(_state())  # type: ignore[arg-type]


async def test_code_step_nonzero_exit_fails_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(self: Any, code: str, language: str = "python", **_: Any) -> Any:
        return CodeResult(stdout="", stderr="Traceback: boom", exit_code=1)

    monkeypatch.setattr("app.tools.code_interpreter.CodeInterpreter.execute", fake_execute)
    with pytest.raises(RuntimeError, match="boom"):
        await CodeStepNode(_Step(), _Ctx()).execute(_state())  # type: ignore[arg-type]


async def test_code_step_without_tenant_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="no tenant"):
        await CodeStepNode(_Step(), _Ctx()).execute({})  # type: ignore[arg-type]
