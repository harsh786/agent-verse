"""The workflow "code" step must never exec() user code in the API/worker process.

Regression: without an execution_environment the step fell back to
``exec(code, {}, {"__builtins__": {}})`` in-process — escapable, so any
workflow author could run code on the control plane with its env secrets.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from app.tools.code_interpreter import CodeResult
from app.workflow.steps import code_step
from app.workflow.steps.code_step import CodeStepNode


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


def _no_exec(*_a: Any, **_k: Any) -> None:
    raise AssertionError("code step must not exec() in-process")


async def test_code_step_runs_in_sandbox_not_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
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

    out = await CodeStepNode(_Step(), _Ctx()).execute({})  # type: ignore[arg-type]

    assert out["step_outputs"]["c1"] == {"doubled": 42}
    assert seen["language"] == "python"
    assert "inputs['n'] * 2" in seen["code"]


async def test_code_step_fails_closed_without_sandbox_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("app.tools.code_interpreter._DOCKER_AVAILABLE", False)
    monkeypatch.setattr(builtins, "exec", _no_exec)

    with pytest.raises(RuntimeError):
        await CodeStepNode(_Step(), _Ctx()).execute({})  # type: ignore[arg-type]


async def test_code_step_nonzero_exit_fails_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(self: Any, code: str, language: str = "python", **_: Any) -> Any:
        return CodeResult(stdout="", stderr="Traceback: boom", exit_code=1)

    monkeypatch.setattr("app.tools.code_interpreter.CodeInterpreter.execute", fake_execute)
    with pytest.raises(RuntimeError, match="boom"):
        await CodeStepNode(_Step(), _Ctx()).execute({})  # type: ignore[arg-type]
