"""CORE-18 unit: the executor checkpoints durably when a DB is wired."""

from __future__ import annotations

from typing import Any

import pytest

from app.coordination.patterns.common import (
    DurablePatternState,
    DurableWorkItem,
    InMemoryPatternCheckpointStore,
)
from app.orchestration.strategy_checkpoint_store import (
    PostgresStrategyCheckpointStore,
    _retryable,
)
from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import ExecutionTerminalState
from app.orchestration.strategy_executor import (
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner
from app.providers.base import CompletionResponse
from tests.orchestration.test_distributed_hitl_gates import _request


def test_executor_uses_the_postgres_store_when_a_db_is_wired() -> None:
    db = object()
    durable = DistributedStrategyExecutor(
        context_store=StrategyGoalContextStore(), checkpoint_db=lambda: db
    )
    store = durable._checkpoint_store_for(_request("supervisor"))
    assert isinstance(store, PostgresStrategyCheckpointStore)

    unwired = DistributedStrategyExecutor(
        context_store=StrategyGoalContextStore(), checkpoint_db=lambda: None
    )
    assert isinstance(
        unwired._checkpoint_store_for(_request("supervisor")), InMemoryPatternCheckpointStore
    )


def test_a_failed_run_resumes_its_unfinished_items_only() -> None:
    state = DurablePatternState(
        session_id="t", execution_id="g", phase="failed", terminal_reason="child_failed",
        work_items=(
            DurableWorkItem(work_item_id="a", safe_summary="a", state="completed",
                            result_reference="r1"),
            DurableWorkItem(work_item_id="b", safe_summary="b", state="failed"),
        ),
    )
    out = _retryable(state)
    assert isinstance(out, DurablePatternState)
    assert out.phase == "executing" and out.terminal_reason is None
    assert [i.state for i in out.work_items] == ["completed", "pending"]


# ---------------------------------------------------------------------------
# Voyager resume: a redelivered voyager run continues from its durable
# checkpoint (no second curriculum, no task re-run, no second publication) and
# its final answer still carries every task's result.
# ---------------------------------------------------------------------------

_V_PLAN = '{"steps": [{"id": "s1", "summary": "collect facts"}, {"id": "s2", "summary": "write"}]}'


class _DurableFake:
    """Stands in for PostgresStrategyCheckpointStore: survives the executor."""

    def __init__(self) -> None:
        self.state: Any = None
        self.answers: dict[str, str] = {}

    async def save(self, state: Any) -> None:
        self.state = state

    async def load(self, session_id: str, execution_id: str) -> Any:
        if self.state is None:
            return None
        if (self.state.session_id, self.state.execution_id) != (session_id, execution_id):
            return None
        return _retryable(self.state)

    async def put_answer(self, ref: str, answer: str) -> None:
        self.answers[ref] = answer

    async def get_answer(self, ref: str) -> str | None:
        return self.answers.get(ref)


class _VoyagerProvider:
    def __init__(self, *, crash_on: str | None) -> None:
        self.crash_on = crash_on
        self.prompts: list[str] = []
        self._default_model = "m"

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = request.messages[0].content
        self.prompts.append(prompt)
        if self.crash_on is not None and self.crash_on in prompt:
            raise ConnectionError("worker lost")
        if prompt.startswith("List 1-"):
            return CompletionResponse(content=_V_PLAN, model="m")
        if prompt.startswith("Combine these task results"):
            return CompletionResponse(content="final: " + prompt.split(":", 1)[1], model="m")
        return CompletionResponse(
            content="result of " + prompt.rsplit("Task:", 1)[-1].strip(), model="m"
        )


async def _voyager_run(durable: _DurableFake, provider: _VoyagerProvider, library: Any) -> Any:
    from tests.orchestration.test_voyager_strategy import CTX, _limits
    from tests.orchestration.test_voyager_strategy import _request as _v_request

    store = StrategyGoalContextStore()
    await store.put(
        "context-v",
        StrategyGoalContext(goal_text="Explain the sky", provider=provider, tenant_ctx=CTX),
    )
    executor = DistributedStrategyExecutor(context_store=store, skill_store=library)
    executor._durable_checkpoints = lambda _request: durable  # a restarted worker's store
    runner = StrategyRunner(
        build_default_registry(), executor=executor, admission=default_distributed_admission
    )
    return await runner.run(_v_request(), _limits())


@pytest.mark.parametrize(
    "crash_on",
    [
        "Task: write",  # mid-curriculum: task 1 done, task 2 lost
        "Combine these task results",  # after publication, during the final answer
    ],
)
async def test_redelivered_voyager_run_resumes_from_its_checkpoint(crash_on: str) -> None:
    from tests.orchestration.test_voyager_strategy import _Library

    durable, library = _DurableFake(), _Library()
    first = await _voyager_run(durable, _VoyagerProvider(crash_on=crash_on), library)
    assert first.terminal_state is not ExecutionTerminalState.SUCCEEDED

    resumed = _VoyagerProvider(crash_on=None)
    second = await _voyager_run(durable, resumed, library)
    assert second.terminal_state is ExecutionTerminalState.SUCCEEDED, (
        second.safe_rationale_summary, resumed.prompts
    )
    # No second curriculum and no re-run of a finished task.
    assert not any(p.startswith("List 1-") for p in resumed.prompts)
    assert not any(p.endswith("Task: collect facts") for p in resumed.prompts)
    # Exactly one skill published across both deliveries.
    assert len(library.published) == 1
    [final] = [p for p in resumed.prompts if p.startswith("Combine")]
    assert "result of collect facts" in final and "result of write" in final


async def test_a_resumed_voyager_run_without_its_curriculum_fails_closed() -> None:
    """A checkpoint whose curriculum is gone must not continue on a new plan."""
    from app.agent.patterns.voyager import VoyagerState
    from tests.orchestration.test_voyager_strategy import _Library

    durable, library = _DurableFake(), _Library()
    durable.state = VoyagerState(
        session_id="tenant-v", execution_id="goal-v1", phase="executing", task_index=1,
        evidence_refs=("strategy-run://gone",),
    )
    provider = _VoyagerProvider(crash_on=None)
    out = await _voyager_run(durable, provider, library)
    assert out.terminal_state is not ExecutionTerminalState.SUCCEEDED
    assert provider.prompts == [] and library.published == []
