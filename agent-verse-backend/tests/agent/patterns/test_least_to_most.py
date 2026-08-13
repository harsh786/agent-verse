from __future__ import annotations

import asyncio

import pytest

from app.agent.patterns.least_to_most import LeastToMostRuntime
from app.agent.patterns.reasoning_contracts import ReasoningContractError, SubproblemState


def _item(identifier: str, dependencies: tuple[str, ...] = ()) -> SubproblemState:
    return SubproblemState(
        subproblem_id=identifier,
        question=f"question {identifier}",
        depends_on=dependencies,
        status="pending",
        answer_ref=None,
    )


@pytest.mark.asyncio
async def test_executes_topologically_with_bounded_context_and_checkpoints() -> None:
    calls: list[tuple[str, dict[str, str], str]] = []
    checkpoints: list[tuple[str, tuple[str, ...]]] = []

    async def solve(item: SubproblemState, deps: dict[str, str], summary: str) -> str:
        calls.append((item.subproblem_id, deps, summary))
        return f"answer-{item.subproblem_id}"

    result = await LeastToMostRuntime(
        checkpoint_callback=lambda cursor, done: checkpoints.append((cursor, done))
    ).execute(
        subproblems=(_item("root"), _item("middle", ("root",)), _item("final", ("middle",))),
        solve=solve,
        synthesize=lambda answers: answers["final"],
    )
    assert result.phase == "completed"
    assert [call[0] for call in calls] == ["root", "middle", "final"]
    assert calls[1][1] == {"root": "answer-root"}
    assert checkpoints[-1] == ("final", ("root", "middle", "final"))


@pytest.mark.asyncio
async def test_resume_skips_completed_and_cancellation_is_terminal() -> None:
    invoked: list[str] = []
    cancelled = asyncio.Event()
    result = await LeastToMostRuntime().execute(
        subproblems=(_item("root"), _item("final", ("root",))),
        completed_answers={"root": "prior"},
        solve=lambda item, _deps, _summary: invoked.append(item.subproblem_id) or "new",
        synthesize=lambda _answers: "done",
    )
    assert result.phase == "completed"
    assert invoked == ["final"]
    cancelled.set()
    stopped = await LeastToMostRuntime().execute(
        subproblems=(_item("root"),),
        solve=lambda *_args: pytest.fail("must not execute"),
        synthesize=lambda _answers: "done",
        cancelled=cancelled,
    )
    assert stopped.phase == "cancelled"


def test_rejects_cycle_and_more_than_eight_items() -> None:
    with pytest.raises(ReasoningContractError, match="cycle"):
        asyncio.run(
            LeastToMostRuntime().execute(
                subproblems=(_item("a", ("b",)), _item("b", ("a",))),
                solve=lambda *_args: "x",
                synthesize=lambda _answers: "x",
            )
        )
    with pytest.raises(ReasoningContractError, match="limit"):
        asyncio.run(
            LeastToMostRuntime().execute(
                subproblems=tuple(_item(str(index)) for index in range(9)),
                solve=lambda *_args: "x",
                synthesize=lambda _answers: "x",
            )
        )
