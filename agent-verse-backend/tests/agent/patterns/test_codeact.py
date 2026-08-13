from __future__ import annotations

import asyncio
import hashlib

import pytest

from app.agent.patterns.codeact import CodeActPhase, CodeActRuntime
from app.execution_environment.models import (
    CodeExecutionObservation,
    CodeExecutionWorkload,
    CodeWorkloadMode,
)


def _workload(number: int, *, repeated: bool = False) -> CodeExecutionWorkload:
    source = "result = 1" if repeated else f"result = {number}"
    return CodeExecutionWorkload.create(
        workload_id=f"workload-{number}",
        mode=CodeWorkloadMode.CODEACT,
        source=source,
        stdin_json=None,
        expected_output_schema={"type": "integer"},
        requested_artifacts=(),
    )


def _observation(workload: CodeExecutionWorkload, number: int) -> CodeExecutionObservation:
    digest = hashlib.sha256(f"observation-{number}".encode()).hexdigest()
    return CodeExecutionObservation(
        workload_id=workload.workload_id,
        source_sha256=workload.source_sha256,
        exit_code=0,
        terminal_state="completed",
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        result_json=number,
        artifact_refs=(),
        cpu_time_ms=1,
        wall_time_ms=1,
        peak_memory_bytes=1,
        denial_codes=(),
        observation_sha256=digest,
    )


class Tool:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, *, workload, **_kwargs):
        self.calls.append(workload.workload_id)
        return _observation(workload, len(self.calls))


@pytest.mark.asyncio
async def test_codeact_completes_with_fresh_bounded_actions() -> None:
    tool = Tool()
    contexts: list[tuple[object, ...]] = []

    def generate(_goal: str, number: int, context: tuple[object, ...]):
        contexts.append(context)
        return _workload(number)

    state, answer = await CodeActRuntime(governed_code_tool=tool).execute(
        goal="calculate",
        invocation=object(),
        generate=generate,
        evaluate=lambda observation: {
            "progress": True,
            "complete": observation.result_json == 2,
        },
        synthesize=lambda result: f"answer:{result}",
    )
    assert state.phase is CodeActPhase.COMPLETED and answer == "answer:2"
    assert tool.calls == ["workload-1", "workload-2"]
    assert contexts[0] == () and len(contexts[1]) == 1


@pytest.mark.asyncio
async def test_two_no_progress_actions_stall_and_cap_is_bounded() -> None:
    tool = Tool()
    state, answer = await CodeActRuntime(governed_code_tool=tool).execute(
        goal="stuck",
        invocation=object(),
        generate=lambda _goal, number, _context: _workload(number, repeated=True),
        evaluate=lambda _observation: {"progress": False, "complete": False},
        synthesize=lambda _: pytest.fail("must not synthesize"),
        maximum_actions=99,
    )
    assert state.phase == "stalled" and answer is None and len(tool.calls) == 2


@pytest.mark.asyncio
async def test_precancel_and_static_denial_prevent_tool_dispatch() -> None:
    tool = Tool()
    cancelled = asyncio.Event()
    cancelled.set()
    stopped, _ = await CodeActRuntime(governed_code_tool=tool).execute(
        goal="x",
        invocation=object(),
        generate=lambda *_args: pytest.fail("must not generate"),
        evaluate=lambda _: {},
        synthesize=lambda _: "x",
        cancelled=cancelled,
    )
    assert stopped.phase == "cancelled"
    denied, _ = await CodeActRuntime(governed_code_tool=tool).execute(
        goal="x",
        invocation=object(),
        generate=lambda *_args: CodeExecutionWorkload.create(
            workload_id="bad",
            mode=CodeWorkloadMode.CODEACT,
            source="open('/etc/passwd').read()",
            stdin_json=None,
            expected_output_schema={},
            requested_artifacts=(),
        ),
        evaluate=lambda _: {},
        synthesize=lambda _: "x",
    )
    assert denied.phase == "failed" and tool.calls == []
