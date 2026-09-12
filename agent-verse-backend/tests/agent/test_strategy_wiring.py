"""P2/P3 wiring: the resolved execution strategy drives planner prompt (A) and
executor tool dispatch (B) through the real AgentGraph."""

from __future__ import annotations

import pytest

from app.agent.execution_strategy import ExecutionStrategy, PlanMode, ToolMode
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="strat-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1", roles=("admin",))

_STRUCTURED_MARKER = "no unmet dependencies can run in parallel"

_STRUCTURED_PLAN = (
    '{"steps": ['
    '{"id": "s1", "description": "search topic A", "depends_on": []},'
    '{"id": "s2", "description": "search topic B", "depends_on": []}'
    "]}"
)


def _planner_system_texts(planner) -> list[str]:
    texts: list[str] = []
    for req in planner.call_history:
        if getattr(req, "system", None):
            texts.append(req.system)
        for m in getattr(req, "messages", []) or []:
            if getattr(m, "role", "") == "system":
                texts.append(m.content)
    return texts


@pytest.mark.asyncio
async def test_structured_strategy_uses_structured_planner_prompt():
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    planner = FakeProvider(responses=[_STRUCTURED_PLAN])
    graph = AgentGraph(
        planner=planner,
        executor=FakeProvider(responses=["result A", "result B"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        execution_strategy=ExecutionStrategy(plan_mode=PlanMode.STRUCTURED, tool_mode=ToolMode.SINGLE),
    )
    await graph.run(goal="Do two independent things", tenant_ctx=T)
    assert any(_STRUCTURED_MARKER in t for t in _planner_system_texts(planner))


@pytest.mark.asyncio
async def test_sequential_strategy_uses_plain_planner_prompt():
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    planner = FakeProvider(responses=['{"steps": ["step one", "step two"]}'])
    graph = AgentGraph(
        planner=planner,
        executor=FakeProvider(responses=["result"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        execution_strategy=ExecutionStrategy(plan_mode=PlanMode.SEQUENTIAL, tool_mode=ToolMode.SINGLE),
    )
    await graph.run(goal="Do a thing", tenant_ctx=T)
    assert not any(_STRUCTURED_MARKER in t for t in _planner_system_texts(planner))


def test_graph_resolves_strategy_from_model_ids():
    """A graph wired with a frontier planner model resolves to STRUCTURED without
    an explicit override; a gpt-oss model resolves to SEQUENTIAL."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    def _graph(model: str) -> AgentGraph:
        planner = FakeProvider()
        planner._default_model = model  # type: ignore[attr-defined]
        ex = FakeProvider()
        ex._default_model = model  # type: ignore[attr-defined]
        vf = FakeProvider()
        vf._default_model = model  # type: ignore[attr-defined]
        return AgentGraph(planner=planner, executor=ex, verifier=vf)

    assert _graph("gpt-5.2")._execution_strategy.plan_mode == PlanMode.STRUCTURED
    assert _graph("openai/gpt-oss-20b")._execution_strategy.plan_mode == PlanMode.SEQUENTIAL


def test_adaptive_strategy_can_be_disabled():
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    planner = FakeProvider()
    planner._default_model = "gpt-5.2"  # type: ignore[attr-defined]
    graph = AgentGraph(
        planner=planner,
        executor=FakeProvider(),
        verifier=FakeProvider(),
        enable_adaptive_strategy=False,
    )
    # disabled -> safe default regardless of a capable model
    assert graph._execution_strategy.plan_mode == PlanMode.SEQUENTIAL
