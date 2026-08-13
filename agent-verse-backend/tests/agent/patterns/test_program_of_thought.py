from __future__ import annotations

import asyncio

import pytest

from app.agent.patterns.program_of_thought import (
    ProgramOfThoughtPhase,
    ProgramOfThoughtRuntime,
)
from app.execution_environment.models import (
    CodeExecutionObservation,
    CodeExecutionWorkload,
    CodeWorkloadMode,
)


def _workload(source: str = "result = 4") -> CodeExecutionWorkload:
    return CodeExecutionWorkload.create(
        workload_id="workload",
        mode=CodeWorkloadMode.PROGRAM_OF_THOUGHT,
        source=source,
        stdin_json=None,
        expected_output_schema={"type": "integer"},
        requested_artifacts=(),
    )


def _observation(*, terminal: str = "completed", result=4) -> CodeExecutionObservation:
    return CodeExecutionObservation(
        workload_id="workload",
        source_sha256=_workload().source_sha256,
        exit_code=0,
        terminal_state=terminal,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        result_json=result,
        artifact_refs=(),
        cpu_time_ms=1,
        wall_time_ms=1,
        peak_memory_bytes=1,
        denial_codes=(),
        observation_sha256="1" * 64,
    )


class Tool:
    def __init__(self, observation: CodeExecutionObservation) -> None:
        self.observation = observation
        self.calls = 0

    async def execute(self, **_kwargs):
        self.calls += 1
        return self.observation


@pytest.mark.asyncio
async def test_one_program_one_execution_and_safe_synthesis() -> None:
    tool = Tool(_observation())
    generated = 0
    checkpoints = []

    def generate():
        nonlocal generated
        generated += 1
        return _workload()

    state, answer = await ProgramOfThoughtRuntime(
        governed_code_tool=tool, checkpoint_callback=checkpoints.append
    ).execute(
        generate=generate,
        invocation=object(),
        synthesize=lambda result: f"answer:{result}",
    )
    assert state.phase is ProgramOfThoughtPhase.COMPLETED
    assert answer == "answer:4"
    assert generated == tool.calls == 1
    assert [item.phase for item in checkpoints] == [
        ProgramOfThoughtPhase.GENERATING,
        ProgramOfThoughtPhase.EXECUTING,
        ProgramOfThoughtPhase.SYNTHESIZING,
        ProgramOfThoughtPhase.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_resume_with_observation_skips_generation_and_execution() -> None:
    tool = Tool(_observation())
    state, answer = await ProgramOfThoughtRuntime(governed_code_tool=tool).execute(
        generate=lambda: pytest.fail("must not regenerate"),
        invocation=object(),
        synthesize=lambda result: str(result),
        workload=_workload(),
        observation=_observation(),
    )
    assert state.phase == "completed" and answer == "4" and tool.calls == 0


@pytest.mark.asyncio
async def test_static_denial_runtime_failure_and_cancellation_are_terminal() -> None:
    tool = Tool(_observation(terminal="failed", result=None))
    denied, _ = await ProgramOfThoughtRuntime(governed_code_tool=tool).execute(
        generate=lambda: _workload("import os\nresult = os.environ"),
        invocation=object(),
        synthesize=lambda _: "x",
    )
    assert denied.phase == "failed" and tool.calls == 0
    failed, _ = await ProgramOfThoughtRuntime(governed_code_tool=tool).execute(
        generate=_workload, invocation=object(), synthesize=lambda _: "x"
    )
    assert failed.phase == "failed"
    cancelled = asyncio.Event()
    cancelled.set()
    stopped, _ = await ProgramOfThoughtRuntime(governed_code_tool=tool).execute(
        generate=lambda: pytest.fail("must not generate"),
        invocation=object(),
        synthesize=lambda _: "x",
        cancelled=cancelled,
    )
    assert stopped.phase == "cancelled"
