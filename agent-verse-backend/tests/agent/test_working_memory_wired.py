"""T1.2 — working memory wired into the executor context (helpers)."""
from __future__ import annotations

from app.agent.state import StepResult, StepStatus
from app.agent.working_memory_wiring import (
    sync_working_memory,
    working_memory_block,
)


def _step(desc: str, output: str) -> StepResult:
    return StepResult(description=desc, output=output, status=StepStatus.COMPLETE)


def test_sync_records_step_outputs_once() -> None:
    ctx: dict = {}
    s1 = _step("search jira", "Found 12 open tickets including JIRA-101")
    s2 = _step("summarize", "Summary written")
    sync_working_memory(ctx, [s1, s2])
    sync_working_memory(ctx, [s1, s2])  # idempotent — no duplicates
    store = ctx["_working_memory"]
    assert len(store) == 2
    assert store[0]["content"].startswith("Found 12 open tickets")


def test_sync_skips_empty_output_and_trims_to_capacity() -> None:
    ctx: dict = {}
    steps = [_step(f"s{i}", f"output number {i}") for i in range(20)]
    steps.append(_step("empty", ""))  # no output → skipped
    sync_working_memory(ctx, steps, capacity=5)
    store = ctx["_working_memory"]
    assert len(store) == 5  # trimmed, oldest dropped
    assert store[-1]["content"] == "output number 19"


def test_block_is_salience_ranked_to_focus() -> None:
    ctx: dict = {}
    sync_working_memory(
        ctx,
        [
            _step("kb", "python machine learning tutorial with scikit-learn"),
            _step("weather", "weather forecast shows rain tomorrow"),
        ],
    )
    block = working_memory_block(ctx, focus="python machine learning", max_chars=400)
    assert "python machine learning" in block
    # most-salient line should lead relative to the query focus
    assert block.index("python machine learning") <= block.index("weather forecast")


def test_block_empty_when_no_working_memory() -> None:
    assert working_memory_block({}, focus="anything") == ""
