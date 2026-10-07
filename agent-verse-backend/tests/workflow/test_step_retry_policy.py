"""Workflow step retries: classification, backoff, per-attempt timeouts, failure records.

Found by the live MONGO-FAIL-TOOLS run (``test_mcp_tool_errors_retry_and_failure_path``):

* every failure was retried, including an authorization denial and a refused
  ``$out`` write (the write re-sent after its approval) — nothing classified a
  failure as retryable or not;
* a skipped step's persisted output was ``null``: its error, how many attempts
  were made and why it stopped were recorded nowhere visible (steps API /
  downstream steps);
* a step skipped before a sibling's approval ran AGAIN when the run resumed —
  every retry repeated and the step's timing restarted, so the hung server's
  3 x 10 s retry policy looked like a single 11 s attempt.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.mcp.client import ToolCallResult, current_idempotency_key
from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import RetryConfig, StepDefinition, WorkflowDefinition
from app.workflow.registry import StepTypeRegistry
from app.workflow.retry_policy import (
    StepAttemptError,
    backoff_schedule,
    classify_error_text,
    classify_failure,
    extract_error_id,
    retry_delay,
    should_retry,
)
from app.workflow.runner import WorkflowRunner
from app.workflow.state import StepStatus, WorkflowRunStatus
from app.workflow.steps.tool_step import tool_failure

_UNAUTHORIZED = (
    "Not authorized: the connector's database user lacks the privilege for this "
    "operation on this database (error id 3b4863b2c150)"
)
_REFUSED = (
    "MongoDB operator '$out' is not allowed (it writes data from an aggregation); "
    "found at 'pipeline[1].$out'"
)
_UNREACHABLE = (
    "Could not reach the MongoDB server (connection refused or timed out, DNS, "
    "firewall, or no primary available) (error id 4175a7a47621)"
)
_RUN = "22222222-2222-2222-2222-222222222222"


# ── Classification ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "kind", "retryable"),
    [
        (_UNAUTHORIZED, "unauthorized", False),
        (_REFUSED, "refused", False),
        (_UNREACHABLE, "timeout", True),
        ("The MongoDB operation timed out (error id abcdef123456)", "timeout", True),
        ("The connection to the MongoDB server failed (error id abcdef123456)", "transport", True),
        ("HTTP 503: upstream down", "server_error", True),
        ("HTTP 502: bad gateway", "server_error", True),
        ("HTTP 429: slow down", "rate_limited", True),
        ("HTTP 401: who are you", "unauthorized", False),
        ("HTTP 403: no", "forbidden", False),
        ("HTTP 404 from https://x/y: missing", "not_found", False),
        ("HTTP 422: bad field", "validation", False),
        ("JSON-RPC error -32602: invalid params", "validation", False),
        ("JSON-RPC error -32603: internal", "server_error", True),
        ("Circuit breaker open for builtin-mongodb:x. Retrying after cooldown.", "unavailable",
         True),
        ("Tool call blocked by data exfiltration guard: secrets", "refused", False),
        ("Connector URL blocked by SSRF guard", "refused", False),
        ("connector 'x' does not expose tool 'y'", "not_found", False),
        ("MongoDB refused the operation (Location40324)", "refused", False),
        ("something odd happened", "error", True),
    ],
)
def test_error_text_classification(message: str, kind: str, retryable: bool) -> None:
    failure = classify_error_text(message)
    assert (failure.kind, failure.retryable) == (kind, retryable)


def test_error_id_is_extracted() -> None:
    assert extract_error_id(_UNAUTHORIZED) == "3b4863b2c150"
    assert extract_error_id("failed: error_id=a6f909f9c763 detail") == "a6f909f9c763"
    assert extract_error_id("no id here") is None
    assert classify_error_text(_UNAUTHORIZED).error_id == "3b4863b2c150"


def test_exception_classification() -> None:
    from app.mcp.client import CircuitBreakerOpenError
    from app.workflow.guardrails import WorkflowGuardrailBlockedError

    request = httpx.Request("GET", "https://x")
    assert classify_failure(TimeoutError("step 's' exceeded timeout 10s")).kind == "timeout"
    assert classify_failure(ConnectionResetError("reset")).retryable is True
    assert classify_failure(PermissionError("missing tenant")).retryable is False
    assert classify_failure(CircuitBreakerOpenError("open")).kind == "unavailable"
    assert classify_failure(WorkflowGuardrailBlockedError("pii")).kind == "policy"
    assert classify_failure(httpx.ConnectTimeout("slow", request=request)).kind == "timeout"
    status_401 = httpx.HTTPStatusError(
        "401", request=request, response=httpx.Response(401, request=request)
    )
    assert classify_failure(status_401).retryable is False
    status_503 = httpx.HTTPStatusError(
        "503", request=request, response=httpx.Response(503, request=request)
    )
    assert classify_failure(status_503).retryable is True
    explicit = StepAttemptError("odd", kind="refused", retryable=False, error_id="e1e1e1e1")
    assert classify_failure(explicit).error_id == "e1e1e1e1"


def test_tool_failure_uses_the_connectors_refusal_status() -> None:
    """A built-in connector's ``status`` decides even when the text is unknown."""
    refused = tool_failure(
        "mongodb_aggregate",
        ToolCallResult(
            tool_name="mongodb_aggregate",
            success=False,
            error="odd wording",
            output={"error": "odd wording", "status": "operator_refused"},
        ),
    )
    assert (refused.failure.kind, refused.failure.retryable) == ("refused", False)
    denied = tool_failure(
        "mongodb_find", ToolCallResult(tool_name="mongodb_find", success=False, error=_UNAUTHORIZED)
    )
    assert denied.failure.kind == "unauthorized"
    assert denied.failure.error_id == "3b4863b2c150"
    hung = tool_failure(
        "mongodb_find", ToolCallResult(tool_name="mongodb_find", success=False, error=_UNREACHABLE)
    )
    assert hung.failure.retryable is True


def test_should_retry_filters() -> None:
    unauthorized = StepAttemptError(_UNAUTHORIZED, kind="unauthorized", retryable=False)
    timeout = TimeoutError("t")
    assert should_retry(RetryConfig(), timeout) is True
    assert should_retry(RetryConfig(), unauthorized) is False
    # fail_on may name a failure kind.
    assert should_retry(RetryConfig(fail_on=["timeout"]), timeout) is False
    # Naming an exception class does not override a non-retryable classification ...
    assert should_retry(RetryConfig(retry_on=["StepAttemptError"]), unauthorized) is False
    # ... naming the failure KIND is an explicit opt-in.
    assert should_retry(RetryConfig(retry_on=["unauthorized"]), unauthorized) is True
    assert should_retry(RetryConfig(retry_on=["timeout"]), RuntimeError("x")) is False


# ── Backoff schedule ─────────────────────────────────────────────────────────


def test_backoff_schedules() -> None:
    expo = RetryConfig(max_attempts=4, backoff="exponential", base_delay_ms=400)
    assert backoff_schedule(expo) == [0.4, 0.8, 1.6]
    linear = RetryConfig(max_attempts=4, backoff="linear", base_delay_ms=400)
    assert backoff_schedule(linear) == [0.4, 0.8, 1.2]
    fixed = RetryConfig(max_attempts=3, backoff="fixed", base_delay_ms=250)
    assert backoff_schedule(fixed) == [0.25, 0.25]
    assert backoff_schedule(RetryConfig(max_attempts=1)) == []


def test_backoff_respects_max_delay() -> None:
    capped = RetryConfig(max_attempts=6, base_delay_ms=1000, max_delay="3s")
    assert capped.max_delay_ms == 3000
    assert backoff_schedule(capped) == [1.0, 2.0, 3.0, 3.0, 3.0]
    uncapped = RetryConfig(max_attempts=3, base_delay_ms=1000, max_delay_ms=0)
    assert retry_delay(uncapped, 40) > 1e9  # no cap, and no overflow


def test_backoff_jitter_with_fixed_randomness() -> None:
    full = RetryConfig(base_delay_ms=1000, jitter=True)
    assert full.jitter == "full"
    assert retry_delay(full, 2, rand=lambda: 0.25) == 0.5
    equal = RetryConfig(base_delay_ms=1000, jitter="equal")
    assert retry_delay(equal, 2, rand=lambda: 0.0) == 1.0
    assert retry_delay(equal, 2, rand=lambda: 1.0) == 2.0
    assert retry_delay(RetryConfig(base_delay_ms=1000), 2, rand=lambda: 0.0) == 2.0


# ── Node wrapper: per-attempt timeouts, records ──────────────────────────────


class _FakeClock:
    """Monotonic time advanced only by the retry loop's (fake) sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(round(delay, 6))
        self.now += delay


def _compiler(**services: Any) -> tuple[WorkflowCompiler, _FakeClock]:
    compiler = WorkflowCompiler(ContextResolver(), **services)
    clock = _FakeClock()
    compiler._sleep = clock.sleep
    compiler._monotonic = clock.monotonic
    compiler._rand = lambda: 0.5
    return compiler, clock


@pytest.fixture
def step_types() -> Any:
    saved = dict(StepTypeRegistry._registry)  # type: ignore[attr-defined]
    yield StepTypeRegistry._registry  # type: ignore[attr-defined]
    StepTypeRegistry._registry = saved  # type: ignore[attr-defined]


class _Recorder:
    """Run store capturing the step rows the node wrapper writes."""

    def __init__(self, prior: dict[str, Any] | None = None) -> None:
        self.prior = prior
        self.finished: dict[str, dict[str, Any]] = {}
        self.attempts: list[dict[str, Any]] = []

    async def get_step_result(self, tenant_id: str, run_id: str, step_id: str) -> Any:
        return self.prior

    async def get_status(self, tenant_id: str, run_id: str) -> str:
        return "running"

    async def record_step_start(self, **kw: Any) -> str:
        return "row"

    async def record_step_attempt(self, **kw: Any) -> bool:
        self.attempts.append(kw)
        return True

    async def record_step_finish(self, *, step_id: str, **kw: Any) -> bool:
        self.finished[step_id] = kw
        return True


_STATE = {"run_id": _RUN, "tenant_id": "t-1", "step_outputs": {}}


@pytest.mark.asyncio
async def test_every_attempt_gets_its_own_timeout_and_the_declared_backoff(
    step_types: dict[str, Any],
) -> None:
    calls: list[int] = []

    class _Hangs:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: Any) -> dict[str, Any]:
            calls.append(1)
            await asyncio.sleep(30)
            return {}

    step_types["hangs_t"] = _Hangs
    store = _Recorder()
    compiler, clock = _compiler(run_store=store)
    step = StepDefinition(
        id="s",
        type="hangs_t",
        timeout="0.05s",
        on_failure="skip",
        retry=RetryConfig(max_attempts=3, backoff="exponential", base_delay_ms=400),
    )
    loop = asyncio.get_running_loop()
    began = loop.time()
    result = await compiler._build_node_fn(step)(dict(_STATE))
    elapsed = loop.time() - began

    assert len(calls) == 3, "each attempt timed out on its own deadline and was retried"
    assert clock.sleeps == [0.4, 0.8], "exponential backoff between the 3 attempts"
    assert elapsed < 1.0, "the backoff went through the injected clock, not real sleeps"
    out = result["step_outputs"]["s"]
    assert out["_skipped"] is True
    assert out["attempts"] == 3
    assert out["error_kind"] == "timeout"
    assert out["reason"] == "retries_exhausted"
    assert "exceeded timeout 0.05s" in out["error"]
    # The row: skipped WITH its output, the attempts and every attempt's record.
    row = store.finished["s"]
    assert row["status"] == StepStatus.SKIPPED
    assert row["output"] == out
    assert row["attempts"] == 3
    assert [a["attempt"] for a in row["attempt_log"]] == [1, 2, 3]
    assert [a.get("retry_in_ms") for a in row["attempt_log"]] == [400, 800, None]
    # While retrying, each failed attempt was published (attempts started so far).
    assert [a["attempts"] for a in store.attempts] == [2, 3]


@pytest.mark.asyncio
async def test_non_retryable_failure_is_tried_once_and_says_why(
    step_types: dict[str, Any],
) -> None:
    calls: list[int] = []

    class _Denied:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: Any) -> dict[str, Any]:
            calls.append(1)
            raise tool_failure(
                "mongodb_find",
                ToolCallResult(tool_name="mongodb_find", success=False, error=_UNAUTHORIZED),
            )

    step_types["denied_t"] = _Denied
    store = _Recorder()
    compiler, clock = _compiler(run_store=store)
    step = StepDefinition(
        id="s", type="denied_t", on_failure="skip",
        retry=RetryConfig(max_attempts=3, base_delay_ms=400),
    )
    out = (await compiler._build_node_fn(step)(dict(_STATE)))["step_outputs"]["s"]

    assert calls == [1]
    assert clock.sleeps == []
    assert out == {
        "_skipped": True,
        "error": _UNAUTHORIZED,
        "error_kind": "unauthorized",
        "retryable": False,
        "error_id": "3b4863b2c150",
        "attempts": 1,
        "max_attempts": 3,
        "reason": "non_retryable",
    }
    assert store.finished["s"]["error"] == _UNAUTHORIZED
    assert store.attempts == []


@pytest.mark.asyncio
async def test_abort_carries_the_failure_detail(step_types: dict[str, Any]) -> None:
    class _Down:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: Any) -> dict[str, Any]:
            raise RuntimeError("HTTP 503: upstream down (error id 0123456789ab)")

    step_types["down_t"] = _Down
    compiler, clock = _compiler()
    step = StepDefinition(
        id="s", type="down_t", on_failure="abort",
        retry=RetryConfig(max_attempts=2, backoff="fixed", base_delay_ms=100),
    )
    with pytest.raises(RuntimeError, match="HTTP 503") as info:
        await compiler._build_node_fn(step)({})
    assert clock.sleeps == [0.1]
    detail = info.value.workflow_step_failure  # type: ignore[attr-defined]
    assert detail["step_id"] == "s"
    assert detail["attempts"] == 2
    assert detail["error_kind"] == "server_error"
    assert detail["error_id"] == "0123456789ab"
    assert detail["reason"] == "retries_exhausted"
    run_error = info.value.workflow_run_error  # type: ignore[attr-defined]
    assert run_error.startswith("HTTP 503: upstream down")
    assert "2 of 2 attempts; server_error; retries exhausted" in run_error


@pytest.mark.asyncio
async def test_pause_records_the_failure_detail_in_the_run_state(
    step_types: dict[str, Any],
) -> None:
    class _Down:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: Any) -> dict[str, Any]:
            raise RuntimeError(_REFUSED)

    step_types["refused_t"] = _Down
    compiler, _clock = _compiler()
    step = StepDefinition(id="s", type="refused_t", retry=RetryConfig(max_attempts=3))
    result = await compiler._build_node_fn(step)({})
    assert result["status"] == WorkflowRunStatus.PAUSED
    assert result["error_detail"]["reason"] == "non_retryable"
    assert result["error_detail"]["error_kind"] == "refused"
    assert "not retried: non-retryable" in result["error"]


@pytest.mark.asyncio
async def test_resume_replays_a_skipped_step_instead_of_running_it_again(
    step_types: dict[str, Any],
) -> None:
    calls: list[int] = []

    class _Counts:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: Any) -> dict[str, Any]:
            calls.append(1)
            return {"step_outputs": {"s": {"ran": True}}}

    step_types["counts_t"] = _Counts
    skipped = {"_skipped": True, "error": "boom", "attempts": 3}
    store = _Recorder(prior={"status": "skipped", "output": skipped, "error": "boom"})
    compiler, _ = _compiler(run_store=store)
    step = StepDefinition(id="s", type="counts_t", on_failure="skip")
    assert await compiler._build_node_fn(step)(dict(_STATE)) == {"step_outputs": {"s": skipped}}
    # A legacy row (skipped with a null output) replays its error.
    store.prior = {"status": "skipped", "output": None, "error": "old boom"}
    replay = await compiler._build_node_fn(step)(dict(_STATE))
    assert replay == {"step_outputs": {"s": {"_skipped": True, "error": "old boom"}}}
    assert calls == []


# ── Engine level: a real workflow of tool steps through the runner ───────────


class _Store:
    """In-memory run store for WorkflowRunner.execute_fresh."""

    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition
        self.run: dict[str, Any] = {"status": "pending"}
        self.steps: dict[str, dict[str, Any]] = {}

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        return {"inputs": {}}

    async def get_status(self, tenant_id: str, run_id: str) -> str:
        return str(self.run["status"])

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        self.run["status"] = str(getattr(status, "value", status))
        self.run.update({k: v for k, v in kw.items() if v is not None})
        return True

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        return "wf"

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self.definition

    async def record_step_start(self, *, step_id: str, **kw: Any) -> str:
        self.steps[step_id] = {"step_id": step_id, "status": "running", "attempts": 1}
        return step_id

    async def record_step_attempt(self, *, step_id: str, attempts: int, **kw: Any) -> bool:
        self.steps[step_id]["attempts"] = attempts
        return True

    async def record_step_finish(self, *, step_id: str, status: Any, **kw: Any) -> bool:
        row = self.steps[step_id]
        row["status"] = str(getattr(status, "value", status))
        row.update({k: v for k, v in kw.items() if v is not None})
        return True

    async def get_step_result(self, tenant_id: str, run_id: str, step_id: str) -> Any:
        return None

    async def list_step_results(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        return list(self.steps.values())


class _Mongo:
    """MCP client: ``flaky`` hangs twice then answers, ``hung`` always hangs,
    ``locked`` denies the read."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.keys: dict[str, list[str | None]] = {}

    async def call_tool_by_name(self, *, tool_name: str, server_id: str | None = None,
                                **kw: Any) -> ToolCallResult:
        sid = str(server_id)
        self.calls[sid] = self.calls.get(sid, 0) + 1
        self.keys.setdefault(sid, []).append(current_idempotency_key())
        if sid == "hung" or (sid == "flaky" and self.calls[sid] <= 2):
            await asyncio.sleep(30)
        if sid == "locked":
            return ToolCallResult(
                tool_name=tool_name, success=False, error=_UNAUTHORIZED,
                output={"error": _UNAUTHORIZED},
            )
        return ToolCallResult(tool_name=tool_name, success=True, output={"docs": [{"n": 1}]})


def _tool(step_id: str, server: str, *, on_failure: str = "skip") -> StepDefinition:
    return StepDefinition(
        id=step_id,
        type="tool",
        tool="mongodb_find",
        server_id=server,
        input={"collection": "orders", "query": {}},
        timeout="0.1s",
        on_failure=on_failure,
        retry=RetryConfig(max_attempts=3, backoff="exponential", base_delay_ms=400),
    )


async def _run(steps: list[StepDefinition]) -> tuple[_Store, _Mongo, _FakeClock]:
    definition = WorkflowDefinition(name="mongo failures", id="wf", steps=steps).to_json()
    store = _Store(definition)
    mongo = _Mongo()
    compiler, clock = _compiler(run_store=store, mcp_client=mongo)
    runner = WorkflowRunner(compiler=compiler, run_store=store)
    await runner.execute_fresh(_RUN, "wf", "t-1")
    return store, mongo, clock


@pytest.mark.asyncio
async def test_workflow_retries_timeouts_and_never_retries_a_denial() -> None:
    store, mongo, clock = await _run(
        [_tool("flaky", "flaky"), _tool("hung", "hung"), _tool("locked", "locked")]
    )

    assert store.run["status"] == "complete", "skipped steps let the run finish"
    # Timed out twice, answered on attempt 3, with the same idempotency key each time.
    assert mongo.calls["flaky"] == 3
    assert mongo.keys["flaky"] == [f"wf:{_RUN}:flaky"] * 3
    flaky = store.steps["flaky"]
    assert flaky["status"] == "complete"
    assert flaky["output"] == {"success": True, "output": {"docs": [{"n": 1}]}, "error": ""}
    assert flaky["attempts"] == 3
    assert [a["error_kind"] for a in flaky["attempt_log"]] == ["timeout", "timeout"]
    # Always hung: 3 attempts, then skipped with the timeout recorded.
    assert mongo.calls["hung"] == 3
    hung = store.steps["hung"]
    assert hung["status"] == "skipped"
    assert hung["attempts"] == 3
    assert hung["output"]["_skipped"] is True
    assert hung["output"]["error_kind"] == "timeout"
    assert hung["output"]["reason"] == "retries_exhausted"
    assert "exceeded timeout 0.1s" in hung["output"]["error"]
    assert "exceeded timeout 0.1s" in hung["error"]
    # Unauthorized: one attempt, not retried, the denial and its error id recorded.
    assert mongo.calls["locked"] == 1
    locked = store.steps["locked"]
    assert locked["status"] == "skipped"
    assert locked["attempts"] == 1
    assert locked["output"]["reason"] == "non_retryable"
    assert locked["output"]["error_kind"] == "unauthorized"
    assert locked["output"]["error_id"] == "3b4863b2c150"
    assert "Not authorized" in locked["output"]["error"]
    # Backoff: two waits for each of flaky and hung (0.4 s, 0.8 s), none for locked.
    assert sorted(clock.sleeps) == [0.4, 0.4, 0.8, 0.8]


@pytest.mark.asyncio
async def test_aborting_tool_step_records_the_failure_on_the_run() -> None:
    store, mongo, _clock = await _run([_tool("locked", "locked", on_failure="abort")])

    assert mongo.calls["locked"] == 1
    assert store.run["status"] == "failed"
    assert store.run["error_step_id"] == "locked"
    assert store.run["error"].startswith(_UNAUTHORIZED)
    assert "1 of 3 attempts; unauthorized; not retried: non-retryable" in store.run["error"]
    detail = store.run["error_detail"]
    assert detail["step_id"] == "locked"
    assert detail["attempts"] == 1
    assert detail["reason"] == "non_retryable"
    assert detail["retryable"] is False
    assert detail["error_id"] == "3b4863b2c150"
    assert store.steps["locked"]["status"] == "failed"


# ── Run stream ───────────────────────────────────────────────────────────────


class _ScriptedRunStore:
    def __init__(self, script: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self.script = script
        self.reads = 0
        self.current = script[0]

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        self.current = self.script[min(self.reads, len(self.script) - 1)]
        self.reads += 1
        return {"run_id": run_id, "status": self.current[0], "outputs": {}}

    async def list_step_results(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        return self.current[1]


@pytest.mark.asyncio
async def test_run_stream_exposes_retries_and_the_final_attempts() -> None:
    from app.api.workflows import _WorkflowStore
    from app.workflow.service import WorkflowService

    log1 = [{"attempt": 1, "error": "t/o", "error_kind": "timeout", "retryable": True,
             "retry_in_ms": 400}]
    log2 = [*log1, {"attempt": 2, "error": "t/o", "error_kind": "timeout", "retryable": True,
                    "retry_in_ms": 800}]
    log3 = [*log2, {"attempt": 3, "error": "t/o", "error_kind": "timeout", "retryable": True,
                    "error_id": "abcdef123456"}]

    def row(status: str, attempts: int, log: list[dict[str, Any]]) -> dict[str, Any]:
        return {"step_id": "hung", "step_type": "tool", "status": status, "started_at": "t0",
                "attempts": attempts, "attempt_log": log, "error": "t/o"}

    store = _ScriptedRunStore([
        ("running", [row("running", 1, [])]),
        ("running", [row("running", 2, log1)]),
        ("running", [row("running", 3, log2)]),
        ("complete", [row("skipped", 3, log3)]),
    ])
    svc = WorkflowService(store=_WorkflowStore(), run_store=store)
    events = [
        e async for e in svc.stream_run_events(
            "t-1", "run-1", poll_interval=0, heartbeat_interval=3600
        )
    ]
    retries = [e for e in events if e["event"] == "step_retrying"]
    assert [(e["attempts"], e["attempt"], e["retry_in_ms"]) for e in retries] == [
        (2, 1, 400),
        (3, 2, 800),
    ]
    skipped = next(e for e in events if e["event"] == "step_skipped")
    assert skipped["attempts"] == 3
    assert skipped["error"] == "t/o"
    assert skipped["error_kind"] == "timeout"
    assert skipped["error_id"] == "abcdef123456"


def test_steps_api_model_exposes_attempts() -> None:
    from app.workflow.router_runs import RunDetailResponse, StepResultResponse

    step = StepResultResponse.model_validate(
        {"step_id": "s", "step_type": "tool", "status": "skipped", "attempts": 3,
         "attempt_log": [{"attempt": 1}], "output": {"_skipped": True}}
    )
    assert step.attempts == 3 and step.attempt_log == [{"attempt": 1}]
    assert StepResultResponse(step_id="s", step_type="t", status="complete").attempts == 1
    run = RunDetailResponse.model_validate(
        {"run_id": "r", "workflow_id": "w", "status": "failed",
         "error_detail": {"attempts": 1, "reason": "non_retryable"}}
    )
    assert run.error_detail == {"attempts": 1, "reason": "non_retryable"}


def test_legacy_should_retry_wrapper_still_honours_guardrails() -> None:
    from app.workflow.guardrails import WorkflowGuardrailBlockedError

    retry = SimpleNamespace(fail_on=[], retry_on=[])
    assert WorkflowCompiler._should_retry(retry, WorkflowGuardrailBlockedError("x")) is False
    assert WorkflowCompiler._should_retry(retry, RuntimeError("transient")) is True
