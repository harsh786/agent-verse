from __future__ import annotations

import asyncio

import pytest

from app.agent.patterns.llm_compiler import LLMCompilerRuntime
from app.agent.patterns.reasoning_contracts import (
    CompiledTask,
    ReasoningContractError,
)
from app.orchestration.strategy_contracts import PatternLimits

CATALOGUE = {
    "lookup": {
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        }
    }
}


def _task(identifier: str, *, dependencies: tuple[str, ...] = ()) -> CompiledTask:
    return CompiledTask(
        task_id=identifier,
        tool_name="lookup",
        arguments={"query": identifier},
        depends_on=dependencies,
        output_schema={"type": "object", "required": ["value"]},
        status="pending",
    )


def _limits() -> PatternLimits:
    return PatternLimits(
        calls=16,
        nodes=16,
        edges=32,
        depth=8,
        fan_out=4,
        rounds=8,
        tokens=16_000,
        duration_seconds=180,
        cost_usd=0.35,
    )


def test_compilation_rejects_unknown_tool_bad_arguments_and_cycle() -> None:
    runtime = LLMCompilerRuntime(
        governed_dispatcher=lambda *_args: None,
        tool_catalogue=CATALOGUE,
    )
    unknown = _task("unknown").model_copy(update={"tool_name": "missing"})
    with pytest.raises(ReasoningContractError, match="unknown compiled tool"):
        runtime.validate_compilation((unknown,))
    bad = _task("bad").model_copy(update={"arguments": {"query": 1}})
    with pytest.raises(ReasoningContractError, match="invalid arguments"):
        runtime.validate_compilation((bad,))
    with pytest.raises(ReasoningContractError, match="cycle"):
        runtime.validate_compilation(
            (_task("a", dependencies=("b",)), _task("b", dependencies=("a",)))
        )


@pytest.mark.asyncio
async def test_executes_stable_dependency_waves_with_idempotency() -> None:
    calls: list[tuple[str, str]] = []

    async def dispatch(_tool: str, arguments: dict[str, object], *, idempotency_key: str):
        calls.append((str(arguments["query"]), idempotency_key))
        return {"value": arguments["query"]}

    result = await LLMCompilerRuntime(
        governed_dispatcher=dispatch, tool_catalogue=CATALOGUE
    ).execute(
        execution_id="execution",
        tasks=(_task("root"), _task("child", dependencies=("root",))),
        limits=_limits(),
        cancelled=asyncio.Event(),
        synthesize=lambda outputs: sorted(outputs),
    )
    assert result.phase == "completed"
    assert calls == [("root", "execution:root"), ("child", "execution:child")]


@pytest.mark.asyncio
async def test_output_schema_failure_and_precancel_fail_closed() -> None:
    async def malformed(*_args, **_kwargs):
        return {"wrong": True}

    runtime = LLMCompilerRuntime(governed_dispatcher=malformed, tool_catalogue=CATALOGUE)
    failed = await runtime.execute(
        execution_id="e",
        tasks=(_task("root"),),
        limits=_limits(),
        cancelled=asyncio.Event(),
        synthesize=lambda _outputs: "x",
    )
    assert failed.phase == "failed"
    cancelled = asyncio.Event()
    cancelled.set()
    stopped = await runtime.execute(
        execution_id="e",
        tasks=(_task("root"),),
        limits=_limits(),
        cancelled=cancelled,
        synthesize=lambda _outputs: "x",
    )
    assert stopped.phase == "cancelled"
