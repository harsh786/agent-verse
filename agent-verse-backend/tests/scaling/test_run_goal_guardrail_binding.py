"""P8-1: run_goal binds the tenant guardrail rule repository before the agent runs.

The FastAPI lifespan binds it for the API; it never runs in a worker, so a
worker-run goal was screened against the in-memory baseline only.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.guardrails_v2 import engine as engine_mod
from app.guardrails_v2.engine import GuardrailsEngine


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> GuardrailsEngine:
    fresh = GuardrailsEngine()
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)
    return fresh


class _CapturingGraph:
    seen: list[bool] = []

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        _CapturingGraph.seen.append(engine_mod.guardrails_engine.has_repository)
        # A governance denial: a final failure (no retry), ends the task here.
        raise PermissionError(
            "Step 'x' requires human approval, but the goal runs in 'bounded-autonomous' "
            "mode where no approval is awaited; the step was not executed."
        )


@pytest.mark.usefixtures("readable_emergency_stop")
def test_run_goal_binds_the_tenant_rule_repository_before_the_agent_runs(
    engine: GuardrailsEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    _CapturingGraph.seen = []
    monkeypatch.setattr(graph_mod, "AgentGraph", _CapturingGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    with (
        patch.object(tasks, "_finalize_owning_mission", _noop),
        patch.object(tasks.run_goal_dlq, "delay", lambda **kw: None),
        patch.object(tasks, "_decrement_after_completion", _noop),
        patch.object(tasks.run_goal, "retry", lambda *a, **k: None),
    ):
        tasks.run_goal.push_request(retries=0, called_directly=False)
        try:
            tasks.run_goal.run("goal-pii-1", "t-worker-pii", "write a contact card")
        finally:
            tasks.run_goal.pop_request()
    assert _CapturingGraph.seen == [True]
