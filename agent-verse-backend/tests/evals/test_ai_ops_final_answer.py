"""P7-2: an eval case reads the goal's final answer the way GET /goals/{id} does.

Live EVAL-GOLDEN: goal 907ba8cf completed with ``step_complete {"output":
"1290"}`` but its case scored an empty output. ``_extract_output`` read only
top-level keys, while the worker event bridge delivers events wrapped as
``{"type", "payload": {...}, "goal_id", "tenant_id"}``; and the goal_complete
event of a distributed-strategy goal carries the answer itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.evals.ai_ops_runner import _extract_output, execute_case
from app.services.result_artifacts import build_result_artifact, final_answer_text
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-final", plan=PlanTier.FREE, api_key_id="k")


def _wrap(event: dict[str, Any], goal_id: str = "g1") -> dict[str, Any]:
    """The shape the worker event bridge delivers to a local subscriber."""
    payload = {k: v for k, v in event.items() if k != "type"}
    return {"type": event["type"], "payload": payload, "goal_id": goal_id, "tenant_id": "t"}


def test_wrapped_step_output_is_the_answer() -> None:
    events = [
        _wrap({"type": "plan_ready", "steps": 1}),
        _wrap({"type": "step_complete", "output": "1290"}),
        _wrap({"type": "goal_complete"}),
    ]
    assert _extract_output(events) == "1290"


def test_goal_complete_answer_wins() -> None:
    events = [
        {"type": "step_complete", "output": "draft"},
        _wrap({"type": "goal_complete", "answer": "final 42", "strategy_id": "moa"}),
    ]
    assert _extract_output(events) == "final 42"


def test_goal_complete_result_object_is_serialised() -> None:
    events = [{"type": "goal_complete", "result": {"total": 3}}]
    assert _extract_output(events) == '{"total": 3}'


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "step_complete", "output": "a"}, {"type": "goal_complete"}],
        [_wrap({"type": "step_complete", "output": "b"}), _wrap({"type": "goal_complete"})],
        [{"type": "step_complete", "output": "x"}, {"type": "goal_complete", "answer": "y"}],
        [
            {"type": "step_complete", "output": "one"},
            {"type": "step_complete", "output": "two"},
            {"type": "goal_complete"},
        ],
    ],
)
def test_eval_answer_matches_get_goal_result_artifact(events: list[dict[str, Any]]) -> None:
    """Same events → the eval scores exactly what GET /goals/{id} shows."""
    artifact = build_result_artifact(goal="g", status="complete", events=events)
    assert final_answer_text(events) == artifact["summary"]
    assert _extract_output(events) == artifact["summary"]


def test_no_answer_is_empty_not_a_placeholder() -> None:
    assert _extract_output([{"type": "goal_complete"}]) == ""


class _WrappedGoalService:
    async def submit_goal(self, **_: Any) -> dict[str, Any]:
        return {"goal_id": "g-907"}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any) -> AsyncIterator[Any]:
        yield _wrap({"type": "step_complete", "output": "1290"}, goal_id)
        yield _wrap({"type": "goal_complete"}, goal_id)


async def test_execute_case_scores_the_wrapped_output() -> None:
    out = await execute_case(
        goal_service=_WrappedGoalService(), tenant_ctx=_CTX, goal="sum it", agent_id=None
    )
    assert out["goal_status"] == "complete"
    assert out["actual_output"] == "1290"
