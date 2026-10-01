"""WF-34: a resumed run keeps the variables and cost of its completed steps.

Resume rebuilds state from the definition defaults and used to restore only the
``step_outputs`` of skipped (already COMPLETE) steps, so a variable set before an
approval was back to its default afterwards and the post-resume steps acted on
it; finalize then wrote only the resumed segment's cost/tokens to the run row.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.steps.transform_step import TransformStepNode
from tests.workflow.test_hitl_multi_gate_resume import (
    _RUN,
    _T,
    _WF,
    _approve,
    _gate,
    _Gateway,
    _HistoryRunStore,
    _Redis,
    _runner,
)


class _DeltaStore(_HistoryRunStore):
    """Keeps the per-step state delta and the run-row telemetry like Postgres."""

    async def record_step_finish(self, *, run_id: str, step_id: str, **kw: Any) -> bool:
        ok = await super().record_step_finish(run_id=run_id, step_id=step_id, **kw)
        for row in reversed(self.rows):
            if row["run_id"] == run_id and row["step_id"] == step_id:
                row["state_delta"] = kw.get("state_delta")
                break
        return ok

    async def update_status(self, run_id: str, status: Any, *, tenant_id: str, **kw: Any) -> bool:
        await super().update_status(run_id, status, tenant_id=tenant_id, **kw)
        for key in ("cost_usd", "tokens_used"):
            if kw.get(key) is not None:
                self.runs[run_id][key] = kw[key]
        return True


def _definition() -> dict[str, Any]:
    return WorkflowDefinition(
        name="vars-across-gates",
        id=_WF,
        vars={"x": "default"},
        steps=[
            StepDefinition(id="setx", type="set_variable", var_name="x", var_value="from-step"),
            StepDefinition(id="pre", type="transform", input={"p": 1}, depends_on=["setx"]),
            _gate("gate1", "pre"),
            StepDefinition(
                id="use", type="transform", input={"v": "{{vars.x}}"}, depends_on=["gate1"]
            ),
            _gate("gate2", "use"),
            StepDefinition(
                id="final", type="transform", input={"v": "{{vars.x}}"}, depends_on=["gate2"]
            ),
        ],
    ).to_json()


@pytest.fixture
def costed_transform(monkeypatch: pytest.MonkeyPatch) -> None:
    original = TransformStepNode.execute

    async def _costed(self: Any, state: Any) -> dict[str, Any]:
        out = await original(self, state)
        return {**out, "cost_usd": 0.25, "tokens_used": 10}

    monkeypatch.setattr(TransformStepNode, "execute", _costed)


@pytest.mark.asyncio
@pytest.mark.usefixtures("costed_transform")
async def test_vars_and_cost_survive_two_resumes() -> None:
    store, gateway, redis = _DeltaStore(_definition()), _Gateway(), _Redis()
    runner = _runner(store, gateway, redis)

    await runner.execute_fresh(_RUN, _WF, _T)
    assert store.runs[_RUN]["status"] == "waiting_hitl"
    assert store.runs[_RUN]["cost_usd"] == pytest.approx(0.25)

    await _approve(runner, "gate1")
    use = await store.get_step_result(_T, _RUN, "use")
    assert use is not None and use["output"] == {"v": "from-step"}, use

    await _approve(runner, "gate2")
    assert store.runs[_RUN]["status"] == "complete"
    final = await store.get_step_result(_T, _RUN, "final")
    assert final is not None and final["output"] == {"v": "from-step"}, final
    # pre + use + final, each executed exactly once.
    assert store.runs[_RUN]["cost_usd"] == pytest.approx(0.75)
    assert store.runs[_RUN]["tokens_used"] == 30
    assert store.executions("setx") == 1


@pytest.mark.asyncio
async def test_completed_step_delta_is_persisted_without_control_keys() -> None:
    store, gateway, redis = _DeltaStore(_definition()), _Gateway(), _Redis()
    runner = _runner(store, gateway, redis)
    await runner.execute_fresh(_RUN, _WF, _T)
    setx = await store.get_step_result(_T, _RUN, "setx")
    assert setx is not None
    assert setx["state_delta"] == {"vars": {"x": "from-step"}}
