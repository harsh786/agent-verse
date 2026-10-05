"""P7-2: an eval case reads the goal's final answer the way GET /goals/{id} does.

Live EVAL-GOLDEN: goal 907ba8cf completed with ``step_complete {"output":
"1290"}`` but its case scored an empty output. ``_extract_output`` read only
top-level keys, while the worker event bridge delivers events wrapped as
``{"type", "payload": {...}, "goal_id", "tenant_id"}``; and the goal_complete
event of a distributed-strategy goal carries the answer itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.evals.ai_ops_jobs import RunConfig, run_step
from app.evals.ai_ops_runner import _extract_output
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
    """A completed goal whose stored events are bridge-wrapped."""

    async def submit_goal(self, **_: Any) -> dict[str, Any]:
        return {"goal_id": "g-907"}

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {"goal_id": goal_id, "status": "complete"}

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        return [
            _wrap({"type": "step_complete", "output": "1290"}, goal_id),
            _wrap({"type": "goal_complete"}, goal_id),
        ]


async def test_a_run_scores_the_wrapped_output() -> None:
    from tests.evals.test_ai_ops_two_slot_worker import Store

    store = Store()
    store.results["r1"] = {"result_id": "r1", "dataset_id": "d1", "status": "queued",
                           "judge": None, "agent_id": None}
    cfg = RunConfig(concurrency=5, poll_seconds=0, lease_seconds=30, case_timeout=60)
    for _ in range(3):
        out = await run_step(store=store, tenant_ctx=_CTX, result_id="r1",
                             goal_service=_WrappedGoalService(), provider=None, cfg=cfg)
        if out["status"] != "running":
            break
    case = store.results["r1"]["cases"][0]
    assert case["goal_status"] == "complete"
    assert case["actual"] == "1290"
