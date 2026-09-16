"""Coverage for LocalSubprocessRunner.run() and .cancel().

The sibling test_local_runner.py only covers small pure-function helpers
(_encode_envelope, _try_forward_event, the env allowlist). This file exercises
the actual subprocess dispatch/parsing logic in .run() and the process
lifecycle branches in .cancel(), all without spawning real subprocesses or
sending real signals:

- asyncio.create_subprocess_exec is mocked to return a fake process whose
  stdout/stderr are async iterables of bytes (mirrors real subprocess streams).
- os.getpgid / os.killpg are patched so no real signal is ever sent, even
  though the fake pid is not a real process.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.execution_environment.envelope import build_envelope
from app.execution_environment.local_runner import (
    LocalSubprocessRunner,
    _kill_process_group,
    _try_forward_event,
)
from app.execution_environment.models import (
    CodeExecutionWorkload,
    CodeWorkloadMode,
    ExecutionFailureReason,
    ExecutionKind,
    ExecutionRequest,
    ExecutionResourceLimits,
    RunnerType,
)

# NOTE: asyncio_mode = "auto" (pyproject.toml) runs `async def` tests without
# an explicit marker. A module-level `pytestmark = pytest.mark.asyncio` would
# also apply to the plain `def` tests below (the _try_forward_event /
# _kill_process_group unit tests) and fail under filterwarnings=error, so it
# is deliberately omitted here.


@pytest.fixture(autouse=True)
def _no_real_signals():
    """Never let a fake pid reach a real os.getpgid/os.killpg syscall."""
    with (
        patch("app.execution_environment.local_runner.os.getpgid", return_value=999999),
        patch("app.execution_environment.local_runner.os.killpg"),
    ):
        yield


class _FakeStream:
    """Mimics asyncio.StreamReader's `async for line in stream` protocol."""

    def __init__(self, lines: list[bytes], delay: float = 0.0) -> None:
        self._lines = lines
        self._delay = delay

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for line in self._lines:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield line


class _FakeProcess:
    def __init__(
        self,
        stdout_lines: list[bytes],
        stderr_lines: list[bytes] | None = None,
        returncode: int = 0,
        pid: int = 4242,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines or [])
        self.pid = pid
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.killed = False

    async def wait(self) -> int | None:
        self.returncode = self._final_returncode
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _make_request(
    *,
    execution_kind: ExecutionKind = ExecutionKind.AGENT_GOAL,
    code_workload: CodeExecutionWorkload | None = None,
    wall_clock_seconds: int = 30,
    output_bytes: int = 10_485_760,
    scoped_redis_prefix: str = "",
) -> ExecutionRequest:
    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="do a thing" if execution_kind is ExecutionKind.AGENT_GOAL else "",
        execution_kind=execution_kind,
        code_workload=code_workload,
        resource_limits=ExecutionResourceLimits(
            wall_clock_seconds=wall_clock_seconds, output_bytes=output_bytes
        ),
        scoped_llm_api_key="sk-secret",
        scoped_db_url="postgresql://user:pass@host/db",
        scoped_redis_prefix=scoped_redis_prefix,
        runner_type=RunnerType.LOCAL,
    )
    return ExecutionRequest(envelope=envelope, runner_type=RunnerType.LOCAL)


async def _run_with_process(request: ExecutionRequest, proc: _FakeProcess):
    runner = LocalSubprocessRunner()
    with patch(
        "app.execution_environment.local_runner.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ) as create_mock:
        result = await runner.run(request)
    return runner, result, create_mock


# ── run(): success path ────────────────────────────────────────────────────────


async def test_run_success_parses_final_result_line() -> None:
    result_line = json.dumps(
        {
            "_result": True,
            "success": True,
            "status": "complete",
            "iterations": 3,
            "plan": ["step1", "step2"],
            "steps": [{"tool": "x"}],
            "verification_feedback": "looks good",
        }
    ).encode()
    proc = _FakeProcess(stdout_lines=[b'{"type": "progress"}', result_line], returncode=0)
    request = _make_request()

    runner, result, create_mock = await _run_with_process(request, proc)

    assert result.success is True
    assert result.status == "complete"
    assert result.iterations == 3
    assert result.plan == ["step1", "step2"]
    assert result.steps == [{"tool": "x"}]
    assert result.verification_feedback == "looks good"
    assert result.exit_code == 0
    assert result.runner_type == "local"
    assert result.capsule_id.startswith("local-")
    assert result.code_observation is None
    assert request.envelope.attempt_id in runner._processes

    _, kwargs = create_mock.call_args
    env = kwargs["env"]
    assert "DATABASE_URL" not in env
    assert "REDIS_URL" not in env
    assert env["_ISOLATED_WORKER_LLM_KEY"] == "sk-secret"
    assert env["_ISOLATED_WORKER_DB_URL"] == "postgresql://user:pass@host/db"
    assert kwargs["start_new_session"] is True


async def test_run_defaults_to_worker_entrypoint_module_for_agent_goal() -> None:
    proc = _FakeProcess(stdout_lines=[json.dumps({"_result": True, "success": True}).encode()])
    request = _make_request(execution_kind=ExecutionKind.AGENT_GOAL)
    _, _, create_mock = await _run_with_process(request, proc)
    args, _ = create_mock.call_args
    bootstrap = args[-1]
    assert "app.execution_environment.worker_entrypoint" in bootstrap


async def test_run_uses_code_worker_module_for_code_interpreter() -> None:
    workload = CodeExecutionWorkload.create(
        workload_id="w1",
        mode=CodeWorkloadMode.CODEACT,
        source="print(1)",
        expected_output_schema={},
    )
    proc = _FakeProcess(stdout_lines=[json.dumps({"_result": True, "success": True}).encode()])
    request = _make_request(execution_kind=ExecutionKind.CODE_INTERPRETER, code_workload=workload)

    runner, result, create_mock = await _run_with_process(request, proc)

    args, _ = create_mock.call_args
    bootstrap = args[-1]
    assert "app.execution_environment.code_worker" in bootstrap
    assert result.success is True
    assert "w1" in runner._processes


async def test_run_falls_back_to_exit_code_when_no_result_line() -> None:
    proc = _FakeProcess(stdout_lines=[b"not json", b"{}"], returncode=1)
    request = _make_request()
    _, result, _ = await _run_with_process(request, proc)
    assert result.success is False
    assert result.status == "failed"
    assert result.exit_code == 1


async def test_run_injects_scoped_redis_prefix() -> None:
    proc = _FakeProcess(stdout_lines=[json.dumps({"_result": True, "success": True}).encode()])
    request = _make_request(scoped_redis_prefix="tenant-42:")
    _, _, create_mock = await _run_with_process(request, proc)
    _, kwargs = create_mock.call_args
    assert kwargs["env"]["_ISOLATED_WORKER_REDIS_URL"] == "tenant-42:"


async def test_run_forwards_typed_events_to_callback() -> None:
    event_line = json.dumps({"type": "tool_call", "name": "search"}).encode()
    result_line = json.dumps({"_result": True, "success": True}).encode()
    proc = _FakeProcess(stdout_lines=[event_line, result_line])
    request = _make_request()

    received: list[dict] = []

    async def _callback(evt: dict) -> None:
        received.append(evt)

    runner = LocalSubprocessRunner()
    with patch(
        "app.execution_environment.local_runner.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        result = await runner.run(request, event_callback=_callback)
        # _try_forward_event schedules the callback fire-and-forget via
        # asyncio.ensure_future — give the loop a tick to run it.
        await asyncio.sleep(0)

    assert result.success is True
    assert received == [{"type": "tool_call", "name": "search"}]


async def test_run_logs_stderr_but_still_returns_result() -> None:
    proc = _FakeProcess(
        stdout_lines=[json.dumps({"_result": True, "success": True}).encode()],
        stderr_lines=[b"warning: something noisy\n"],
        returncode=0,
    )
    request = _make_request()
    _, result, _ = await _run_with_process(request, proc)
    assert result.success is True


# ── run(): resource limit ───────────────────────────────────────────────────────


async def test_run_stops_and_reports_resource_limit_hit() -> None:
    lines = [b"x" * 20 for _ in range(5)]
    proc = _FakeProcess(stdout_lines=lines, returncode=0)
    request = _make_request(output_bytes=10)

    _, result, _ = await _run_with_process(request, proc)

    assert result.success is False
    assert result.status == "failed"
    assert result.resource_limit_hit is True
    assert result.failure_reason == ExecutionFailureReason.RESOURCE_LIMIT


# ── run(): timeout ───────────────────────────────────────────────────────────────


async def test_run_timeout_kills_process_group() -> None:
    proc = _FakeProcess(stdout_lines=[b"late"])
    request = _make_request(wall_clock_seconds=5)
    runner = LocalSubprocessRunner()
    with (
        patch(
            "app.execution_environment.local_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "app.execution_environment.local_runner.asyncio.wait_for",
            new=AsyncMock(side_effect=TimeoutError),
        ),
    ):
        result = await runner.run(request)

    assert result.timeout_hit is True
    assert result.status == "failed"
    assert result.failure_reason == ExecutionFailureReason.TIMEOUT


# ── run(): unexpected error ──────────────────────────────────────────────────────


async def test_run_unexpected_error_returns_internal_error_result() -> None:
    request = _make_request()
    runner = LocalSubprocessRunner()
    with patch(
        "app.execution_environment.local_runner.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await runner.run(request)

    assert result.success is False
    assert result.status == "failed"
    assert result.failure_reason == ExecutionFailureReason.INTERNAL_ERROR
    assert result.error_message == "boom"


# ── cancel() ─────────────────────────────────────────────────────────────────────


async def test_cancel_when_no_process_found() -> None:
    runner = LocalSubprocessRunner()
    receipt = await runner.cancel("missing-workload", "user_requested")
    assert receipt.process_group_terminated is True
    assert receipt.cleanup_state == "complete"


async def test_cancel_returns_cached_receipt_on_second_call() -> None:
    runner = LocalSubprocessRunner()
    first = await runner.cancel("wl-1", "reason")
    second = await runner.cancel("wl-1", "other reason")
    assert first is second


async def test_cancel_already_terminated_process() -> None:
    runner = LocalSubprocessRunner()
    proc = _FakeProcess(stdout_lines=[], returncode=0)
    proc.returncode = 0  # already exited before cancel() is even called
    runner._processes["wl-2"] = proc
    receipt = await runner.cancel("wl-2", "done")
    assert receipt.process_group_terminated is True
    assert receipt.cleanup_state == "complete"


async def test_cancel_terminates_running_process_gracefully() -> None:
    runner = LocalSubprocessRunner()
    proc = _FakeProcess(stdout_lines=[], returncode=0)
    runner._processes["wl-3"] = proc
    receipt = await runner.cancel("wl-3", "user_stop")
    assert receipt.process_group_terminated is True
    assert receipt.cleanup_state == "complete"
    assert proc.returncode == 0


async def test_cancel_kills_process_group_on_termination_timeout() -> None:
    runner = LocalSubprocessRunner()
    proc = _FakeProcess(stdout_lines=[], returncode=-9)
    runner._processes["wl-4"] = proc
    with patch(
        "app.execution_environment.local_runner.asyncio.wait_for",
        new=AsyncMock(side_effect=TimeoutError),
    ):
        receipt = await runner.cancel("wl-4", "force")
    assert receipt.cleanup_state == "complete"
    assert receipt.process_group_terminated is True


# ── _kill_process_group() exception fallbacks ───────────────────────────────────


async def test_kill_process_group_swallows_process_lookup_error() -> None:
    class _Proc:
        pid = 111

    with patch(
        "app.execution_environment.local_runner.os.getpgid",
        side_effect=ProcessLookupError,
    ):
        _kill_process_group(_Proc())  # must not raise


async def test_kill_process_group_falls_back_to_proc_kill_on_generic_error() -> None:
    killed = {"v": False}

    class _Proc:
        pid = 222

        def kill(self) -> None:
            killed["v"] = True

    with patch(
        "app.execution_environment.local_runner.os.getpgid",
        side_effect=RuntimeError("permission denied"),
    ):
        _kill_process_group(_Proc())

    assert killed["v"] is True


# ── LocalSubprocessHealthCheck timeout ───────────────────────────────────────────


async def test_health_check_unexpected_error_before_subprocess_reports_unhealthy() -> None:
    with patch(
        "app.execution_environment.local_runner.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=OSError("fork failed")),
    ):
        runner = LocalSubprocessRunner()
        status = await runner.health_check.check()

    assert status.healthy is False
    assert "fork failed" in status.message


async def test_health_check_timeout_reports_unhealthy() -> None:
    proc = _FakeProcess(stdout_lines=[])
    with (
        patch(
            "app.execution_environment.local_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "app.execution_environment.local_runner.asyncio.wait_for",
            new=AsyncMock(side_effect=TimeoutError),
        ),
    ):
        runner = LocalSubprocessRunner()
        status = await runner.health_check.check()

    assert status.healthy is False
    assert "timed out" in status.message


async def test_cancel_reports_quarantined_when_process_survives() -> None:
    class _StuckProcess(_FakeProcess):
        async def wait(self) -> int | None:  # process refuses to die
            return None

    runner = LocalSubprocessRunner()
    proc = _StuckProcess(stdout_lines=[])
    runner._processes["wl-5"] = proc
    receipt = await runner.cancel("wl-5", "force")
    assert receipt.process_group_terminated is False
    assert receipt.cleanup_state == "quarantined"


# ── _try_forward_event() edge cases not covered by test_local_runner.py ────────


def test_try_forward_event_skips_dict_without_type_key() -> None:
    received: list[dict] = []

    def cb(evt: dict) -> None:
        received.append(evt)

    _try_forward_event(b'{"no_type_here": true}', cb)
    assert received == []


def test_try_forward_event_swallows_invalid_json() -> None:
    # Must not raise even though the line is not valid JSON at all.
    _try_forward_event(b"not-json-at-all {{{", lambda evt: None)
