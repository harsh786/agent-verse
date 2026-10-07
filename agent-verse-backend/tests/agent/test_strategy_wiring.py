"""P2/P3 wiring: the resolved execution strategy drives planner prompt (A) and
executor tool dispatch (B) through the real AgentGraph."""

from __future__ import annotations

import pytest

from app.agent.execution_strategy import ExecutionStrategy, PlanMode, ToolMode
from app.ai_router.selection import ordered_configured_models as _real_ordered_models
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


def _register_reasoning_model(monkeypatch, model_id: str) -> None:
    import app.ai_router.selection as sel
    from app.ai_router.models import ModelCapability, ModelEndpoint
    from app.ai_router.registry import model_registry

    # tests/agent/conftest.py stubs the configured-model lookup out (hermetic
    # routing for the agent unit tests); this test needs the real registry.
    monkeypatch.setattr(sel, "ordered_configured_models", _real_ordered_models)
    # The model registered below is removed again when the test ends.
    monkeypatch.setattr(model_registry, "_configured", dict(model_registry._configured))

    # Keep the lazy re-seed from the store from replacing the configured set, and
    # no ambient env pin / deployment role map (an explicit choice suppresses
    # Strategy C's automatic routing by design).
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(
        "app.ai_router.deployment_roles.deployment_role_models", lambda *a, **k: {}
    )
    for var in ("NVIDIA_API_KEY", "NVIDIA_MODEL", "OPENAI_BASE_URL", "OPENAI_MODEL",
                "DEFAULT_MODEL", "DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL",
                "DEFAULT_VERIFICATION_MODEL"):
        monkeypatch.delenv(var, raising=False)

    # Its own OpenAI-compatible endpoint makes it servable (eligible) without any
    # provider key in the environment.
    model_registry.register_configured(ModelEndpoint(
        provider="onprem", model_id=model_id, display_name=model_id,
        capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE],
        supports_tools=True, cost_per_1k_input=0.0001, quality_score=0.6,
        base_url="http://127.0.0.1:9/v1", extra={"source": "manual"},
    ))


@pytest.mark.asyncio
async def test_strategy_c_routes_verifier_to_fast_model(monkeypatch):
    """Strategy C: the verifier request uses the strategy's nominated fast model.

    The nominee must be a model the deployment configured (Model Registry); a
    strategy can no longer swap in an arbitrary model id.
    """
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    _register_reasoning_model(monkeypatch, "fast-verifier")

    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do it"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=verifier,
        execution_strategy=ExecutionStrategy(
            plan_mode=PlanMode.SEQUENTIAL, tool_mode=ToolMode.SINGLE, verifier_model="fast-verifier"
        ),
    )
    await graph.run(goal="Do a thing", tenant_ctx=T)
    models = [getattr(r, "model", "") for r in verifier.call_history]
    assert "fast-verifier" in models


@pytest.mark.asyncio
async def test_strategy_c_never_routes_to_an_unconfigured_model():
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do it"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=verifier,
        execution_strategy=ExecutionStrategy(
            plan_mode=PlanMode.SEQUENTIAL, tool_mode=ToolMode.SINGLE,
            verifier_model="not-configured-anywhere",
        ),
    )
    await graph.run(goal="Do a thing", tenant_ctx=T)
    models = [getattr(r, "model", "") for r in verifier.call_history]
    assert "not-configured-anywhere" not in models


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
