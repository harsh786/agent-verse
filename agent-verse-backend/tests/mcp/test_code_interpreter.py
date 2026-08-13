from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.execution_environment.models import (
    CodeExecutionObservation,
    CodeExecutionWorkload,
    CodeWorkloadMode,
)
from app.mcp.code_interpreter import (
    CodeInterpreterDenied,
    CodeInterpreterTool,
    GovernedToolInvocation,
)


def _workload() -> CodeExecutionWorkload:
    return CodeExecutionWorkload.create(
        workload_id="workload",
        mode=CodeWorkloadMode.PROGRAM_OF_THOUGHT,
        source="result = 4",
        stdin_json=None,
        expected_output_schema={"type": "integer"},
        requested_artifacts=(),
    )


def _invocation(**updates: object) -> GovernedToolInvocation:
    values: dict[str, object] = {
        "tenant_id": "tenant",
        "goal_id": "goal",
        "strategy_execution_id": "execution",
        "strategy_id": "program_of_thought",
        "policy_version": "v1",
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
        "correlation_id": "correlation",
        "causation_id": "causation",
    }
    values.update(updates)
    return GovernedToolInvocation.model_validate(values)


class Scheduler:
    def __init__(self) -> None:
        self.calls = 0

    async def schedule(self, envelope):
        self.calls += 1
        workload = envelope.code_workload
        assert workload is not None
        return CodeExecutionObservation(
            workload_id=workload.workload_id,
            source_sha256=workload.source_sha256,
            exit_code=0,
            terminal_state="completed",
            stdout="api_key=secret",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            result_json=4,
            artifact_refs=(),
            cpu_time_ms=1,
            wall_time_ms=2,
            peak_memory_bytes=3,
            denial_codes=(),
            observation_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_governed_tool_sanitizes_and_deduplicates() -> None:
    scheduler = Scheduler()
    audit: list[str] = []
    tool = CodeInterpreterTool(
        scheduler=scheduler, audit=lambda event, _payload: audit.append(event)
    )
    first = await tool.execute(invocation=_invocation(), workload=_workload())
    duplicate = await tool.execute(invocation=_invocation(), workload=_workload())
    assert first == duplicate and scheduler.calls == 1
    assert "secret" not in first.stdout
    assert audit == ["code.requested", "code.completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("authenticated", False, "unauthenticated"),
        ("authorized", False, "unauthorized"),
        ("feature_enabled", False, "feature_disabled"),
        ("classification_allowed", False, "classification_denied"),
        ("approval_state", "pending", "approval_pending"),
        ("budget_available", False, "budget_exhausted"),
        ("cancelled", True, "cancelled"),
    ),
)
async def test_governance_denials_never_schedule(field: str, value: object, code: str) -> None:
    scheduler = Scheduler()
    tool = CodeInterpreterTool(scheduler=scheduler, audit=lambda *_args: None)
    with pytest.raises(CodeInterpreterDenied, match=code):
        await tool.execute(invocation=_invocation(**{field: value}), workload=_workload())
    assert scheduler.calls == 0
