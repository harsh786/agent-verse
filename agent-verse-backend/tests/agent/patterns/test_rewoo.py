from __future__ import annotations

import asyncio

import pytest

from app.agent.patterns.reasoning_contracts import ReasoningContractError, ToolPlanStep
from app.agent.patterns.rewoo import ReWOORuntime


def _step(
    identifier: str,
    variable: str,
    *,
    dependencies: tuple[str, ...] = (),
    arguments: dict[str, object] | None = None,
) -> ToolPlanStep:
    return ToolPlanStep(
        step_id=identifier,
        tool_name=f"tool_{identifier}",
        arguments=arguments or {},
        depends_on=dependencies,
        output_variable=variable,
    )


def test_plan_hash_is_canonical_and_variables_require_ancestor() -> None:
    plan = (_step("a", "first"), _step("b", "second", dependencies=("a",)))
    ordered, digest = ReWOORuntime.freeze_plan(plan)
    assert ordered == plan
    assert ReWOORuntime.freeze_plan(plan)[1] == digest
    with pytest.raises(ReasoningContractError, match="unknown variable"):
        ReWOORuntime.validate_variables(
            (_step("a", "first", arguments={"bad": "${missing}"}),)
        )
    with pytest.raises(ReasoningContractError, match="forward variable"):
        ReWOORuntime.validate_variables(
            (
                _step("a", "first", arguments={"bad": "${second}"}),
                _step("b", "second"),
            )
        )


@pytest.mark.asyncio
async def test_wave_execution_resolution_idempotency_and_resume() -> None:
    calls: list[tuple[str, dict[str, object], str]] = []

    async def dispatch(tool: str, arguments: dict[str, object], *, idempotency_key: str):
        calls.append((tool, arguments, idempotency_key))
        return arguments.get("value", "root-output")

    plan = (
        _step("root", "root_value"),
        _step(
            "child",
            "child_value",
            dependencies=("root",),
            arguments={"value": "${root_value}"},
        ),
    )
    result = await ReWOORuntime(governed_dispatcher=dispatch).execute(
        execution_id="execution",
        plan=plan,
        completed_outputs={"root_value": "prior"},
        synthesize=lambda outputs: outputs["child_value"],
    )
    assert result.phase == "completed"
    assert calls == [("tool_child", {"value": "prior"}, "execution:child")]


@pytest.mark.asyncio
async def test_hash_mismatch_cancellation_and_tool_denial_fail_closed() -> None:
    plan = (_step("root", "value"),)
    runtime = ReWOORuntime(governed_dispatcher=lambda *_args, **_kwargs: "x")
    mismatch = await runtime.execute(
        execution_id="e", plan=plan, expected_plan_hash="bad", synthesize=lambda _: "x"
    )
    assert mismatch.terminal_reason == "plan_hash_mismatch"
    cancelled = asyncio.Event()
    cancelled.set()
    stopped = await runtime.execute(
        execution_id="e", plan=plan, synthesize=lambda _: "x", cancelled=cancelled
    )
    assert stopped.phase == "cancelled"

    async def deny(*_args, **_kwargs):
        raise PermissionError("denied")

    denied = await ReWOORuntime(governed_dispatcher=deny).execute(
        execution_id="e", plan=plan, synthesize=lambda _: "x"
    )
    assert denied.phase == "failed"
    assert denied.safe_evidence["cancelled_dependency_ids"] == ["root"]
