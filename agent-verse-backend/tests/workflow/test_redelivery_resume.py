"""WF-14 (workflow runs): a crash-redelivered ``execute_workflow_run`` (the task
is acks_late) continues from the persisted step results instead of restarting
from step 0, and never re-runs a run that already finished or is waiting on an
approval.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.dsl import StepDefinition, WorkflowDefinition
from tests.workflow.test_hitl_multi_gate_resume import (
    _RUN,
    _T,
    _WF,
    _Gateway,
    _HistoryRunStore,
    _Redis,
    _runner,
)


def _linear() -> dict[str, Any]:
    return WorkflowDefinition(
        name="linear",
        id=_WF,
        steps=[
            StepDefinition(
                id="pre", type="emit_event", event_channel_out="c", event_payload={"n": 1}
            ),
            StepDefinition(id="mid", type="transform", input={"m": 1}, depends_on=["pre"]),
            StepDefinition(id="after", type="transform", input={"a": 1}, depends_on=["mid"]),
        ],
    ).to_json()


@pytest.mark.asyncio
async def test_redelivered_run_skips_steps_the_dead_worker_completed() -> None:
    store, redis = _HistoryRunStore(_linear()), _Redis()
    # The first worker finished pre and mid, then died before 'after' (status
    # still 'running'); the broker redelivers the task.
    store.runs[_RUN]["status"] = "running"
    store.rows += [
        {"run_id": _RUN, "step_id": "pre", "status": "complete", "output": {"channel": "c"}},
        {"run_id": _RUN, "step_id": "mid", "status": "complete", "output": {"m": 1}},
    ]

    await _runner(store, _Gateway(), redis).execute_fresh(_RUN, _WF, _T)

    assert store.runs[_RUN]["status"] == "complete"
    assert redis.published == []  # pre's side effect was not replayed
    assert store.executions("pre") == 1 and store.executions("mid") == 1
    assert store.executions("after") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["waiting_hitl", "complete", "failed"])
async def test_redelivery_of_a_suspended_or_finished_run_is_a_no_op(status: str) -> None:
    from tests.workflow.test_hitl_multi_gate_resume import _definition

    store, gateway, redis = _HistoryRunStore(_definition()), _Gateway(), _Redis()
    store.runs[_RUN]["status"] = status

    await _runner(store, gateway, redis).execute_fresh(_RUN, _WF, _T)

    assert store.runs[_RUN]["status"] == status
    assert gateway.created == []  # no duplicate approval
    assert redis.published == [] and store.rows == []
