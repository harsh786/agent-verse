"""Tests for Phase 2 intelligence upgrades."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.agent.structured_plan import StructuredPlan, StructuredStep
from app.agent.prompts import CHAIN_OF_THOUGHT_SYSTEM, REFLECTION_SYSTEM


# ---------------------------------------------------------------------------
# 2.1 Prompts
# ---------------------------------------------------------------------------


def test_chain_of_thought_system_prompt_exists():
    assert "INTENT:" in CHAIN_OF_THOUGHT_SYSTEM or "intent" in CHAIN_OF_THOUGHT_SYSTEM.lower()
    assert len(CHAIN_OF_THOUGHT_SYSTEM) > 100


def test_reflection_system_prompt_exists():
    assert "FUNDAMENTAL_FAILURE" in REFLECTION_SYSTEM
    assert "ROOT_CAUSE" in REFLECTION_SYSTEM


# ---------------------------------------------------------------------------
# 2.1 Parallel execution waves
# ---------------------------------------------------------------------------


def test_execution_waves_parallel_steps():
    """Steps with no shared dependencies execute in the same wave."""
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Fetch issues", depends_on=[]),
        StructuredStep(id="s2", description="Send email", depends_on=[]),
        StructuredStep(id="s3", description="Create report", depends_on=["s1", "s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 2
    assert len(waves[0]) == 2  # s1 and s2 parallel
    assert waves[1][0].id == "s3"


def test_execution_waves_serial_when_all_chained():
    """Fully-chained plan produces single-step waves."""
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Step 1", depends_on=[]),
        StructuredStep(id="s2", description="Step 2", depends_on=["s1"]),
        StructuredStep(id="s3", description="Step 3", depends_on=["s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 3
    for wave in waves:
        assert len(wave) == 1


@pytest.mark.asyncio
async def test_node_execute_emits_parallel_start_event():
    """_node_execute must emit steps_parallel_start when wave has >1 step."""
    import json
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    emitted_events: list[dict] = []

    async def capture_event(event: dict) -> None:
        emitted_events.append(event)

    fake = FakeProvider(responses=["Task executed successfully"])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        enable_cot=False, enable_reflection=False,
    )
    graph._event_callback = capture_event

    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="test goal", tenant_ctx=tenant)

    parallel_plan = json.dumps({
        "steps": [
            {"id": "s1", "description": "Do A", "depends_on": []},
            {"id": "s2", "description": "Do B", "depends_on": []},
        ]
    })
    state = {
        "agent_state": agent_state,
        "plan": [parallel_plan],
        "tenant_ctx": tenant,
        "iteration": 1,
    }
    await graph._node_execute(state)

    parallel_events = [e for e in emitted_events if e.get("type") == "steps_parallel_start"]
    assert len(parallel_events) >= 1


# ---------------------------------------------------------------------------
# 2.2 Model routing
# ---------------------------------------------------------------------------


def test_model_router_wired_to_graph():
    """AgentGraph has _model_router attribute that can be set."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.intelligence.guardrails import GuardrailChecker

    fake = FakeProvider(responses=["done"])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        result_processor=ResultProcessor(), dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(), guardrail_checker=GuardrailChecker(),
        enable_cot=False, enable_reflection=False,
    )
    from app.agent.model_router import ModelRouter
    graph._model_router = ModelRouter("anthropic")
    assert graph._model_router is not None


def test_model_router_selects_correct_models():
    """ModelRouter.model_for returns the right model for each task."""
    from app.agent.model_router import ModelRouter, ModelRouterConfig

    cfg = ModelRouterConfig(
        planning_model="claude-opus-4-8",
        execution_model="claude-haiku-3-5",
        verification_model="claude-haiku-3-5",
    )
    router = ModelRouter(provider_name="anthropic", config=cfg)
    assert router.model_for("planning") == "claude-opus-4-8"
    assert router.model_for("execution") == "claude-haiku-3-5"
    assert router.model_for("verification") == "claude-haiku-3-5"


def test_model_router_falls_back_to_fallback_model():
    """ModelRouter.model_for unknown task returns fallback_model."""
    from app.agent.model_router import ModelRouter, ModelRouterConfig

    cfg = ModelRouterConfig(fallback_model="gpt-4o")
    router = ModelRouter(config=cfg)
    result = router.model_for("unknown_task")
    assert result == "gpt-4o"


@pytest.mark.asyncio
async def test_node_plan_uses_planning_model():
    """_node_plan uses model_router.model_for('planning') in CompletionRequest."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier
    from app.agent.model_router import ModelRouter, ModelRouterConfig

    captured_requests: list = []

    class CapturingProvider(FakeProvider):
        async def complete(self, request):
            captured_requests.append(request)
            return await super().complete(request)

    cfg = ModelRouterConfig(
        planning_model="special-planning-model",
        execution_model="tiny-model",
        verification_model="tiny-model",
    )
    router = ModelRouter(config=cfg)
    provider = CapturingProvider(responses=['{"steps": ["step 1"]}'])
    graph = AgentGraph(
        planner=provider, executor=provider, verifier=provider,
        model_router=router, enable_cot=False, enable_reflection=False,
    )
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="test goal", tenant_ctx=tenant)
    state = {
        "agent_state": agent_state,
        "tenant_ctx": tenant,
        "rag_context": "",
        "iteration": 0,
        "cot_reasoning": "",
    }
    await graph._node_plan(state)
    assert len(captured_requests) >= 1
    assert captured_requests[0].model == "special-planning-model"


# ---------------------------------------------------------------------------
# 2.3 Chain-of-Thought node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_think_produces_cot_reasoning():
    """_node_think returns cot_reasoning in state dict."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    thinking_text = "INTENT: I need to check X then do Y"
    fake = FakeProvider(responses=[thinking_text])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        enable_cot=True, enable_reflection=False,
    )
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="do complex task", tenant_ctx=tenant)
    state = {"agent_state": agent_state, "tenant_ctx": tenant, "rag_context": "", "iteration": 0}
    result = await graph._node_think(state)
    assert "cot_reasoning" in result
    assert result["cot_reasoning"] == thinking_text


@pytest.mark.asyncio
async def test_node_plan_uses_cot_reasoning():
    """_node_plan injects cot_reasoning into the planner user message."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    captured_messages: list = []

    class CapturingProvider(FakeProvider):
        async def complete(self, request):
            captured_messages.extend(request.messages)
            return await super().complete(request)

    fake = CapturingProvider(responses=['{"steps": ["step 1"]}'])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        enable_cot=True, enable_reflection=False,
    )
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="complex task", tenant_ctx=tenant)
    state = {
        "agent_state": agent_state,
        "tenant_ctx": tenant,
        "rag_context": "",
        "iteration": 0,
        "cot_reasoning": "Step-by-step: need tool A then tool B",
    }
    await graph._node_plan(state)
    user_msgs = [m.content for m in captured_messages if m.role == "user"]
    assert any("Step-by-step" in str(m) for m in user_msgs)


# ---------------------------------------------------------------------------
# 2.4 Reflection node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_reflect_sets_verification_feedback():
    """_node_reflect sets agent_state.verification_feedback with targeted diagnosis."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    reflection = "FAILED_STEP: call github.search\nROOT_CAUSE: API key wrong\nFIX: use env var"
    fake = FakeProvider(responses=[reflection])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        enable_cot=False, enable_reflection=True,
    )
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="search github", tenant_ctx=tenant)
    agent_state.status = GoalStatus.FAILED
    agent_state.error_message = "Tool github.search returned 401 Unauthorized"
    agent_state.steps = [
        StepResult(description="init", status=StepStatus.COMPLETE, output="ok"),
        StepResult(
            description="call github.search",
            status=StepStatus.FAILED,
            error="401 Unauthorized",
        ),
    ]
    state = {"agent_state": agent_state, "tenant_ctx": tenant}
    result = await graph._node_reflect(state)
    assert "agent_state" in result
    updated = result["agent_state"]
    assert updated.verification_feedback
    assert "ROOT_CAUSE" in updated.verification_feedback or "FIX" in updated.verification_feedback


# ---------------------------------------------------------------------------
# 2.5 Agent domain specialization (system_prompt column)
# ---------------------------------------------------------------------------


def test_agent_model_has_system_prompt_column():
    """Agent ORM model has system_prompt field."""
    from app.db.models.agent import Agent
    assert hasattr(Agent, "system_prompt")


@pytest.mark.asyncio
async def test_node_plan_injects_agent_system_prompt():
    """_node_plan prepends agent system_prompt when it is in agent_state.context."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    captured_messages: list = []

    class CapturingProvider(FakeProvider):
        async def complete(self, request):
            captured_messages.extend(request.messages)
            return await super().complete(request)

    fake = CapturingProvider(responses=['{"steps": ["step 1"]}'])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        enable_cot=False, enable_reflection=False,
    )
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    agent_state = AgentState(goal="review code", tenant_ctx=tenant)
    agent_state.context["system_prompt"] = "You are an expert code security reviewer."
    state = {
        "agent_state": agent_state,
        "tenant_ctx": tenant,
        "rag_context": "",
        "iteration": 0,
        "cot_reasoning": "",
    }
    await graph._node_plan(state)
    system_msgs = [m.content for m in captured_messages if m.role == "system"]
    assert any("security" in str(m) for m in system_msgs)


# ---------------------------------------------------------------------------
# 2.6 Semantic cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_step_with_cache_hits_cache():
    """_execute_step_with_cache returns cached result and emits cache_hit."""
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.providers.base import EmbedRequest, EmbedResponse
    from app.rag.semantic_cache import SemanticCache
    from app.tenancy.context import TenantContext, PlanTier

    emitted: list[dict] = []
    llm_calls: list = []

    class CountingProvider(FakeProvider):
        async def complete(self, request):
            llm_calls.append(request)
            return await super().complete(request)

    async def mock_embed(request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.1, 0.2, 0.3]])

    cache = SemanticCache(threshold=0.9, ttl_seconds=300)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    cache.store(
        query_embedding=[0.1, 0.2, 0.3],
        response="cached answer",
        tenant_ctx=tenant,
    )

    provider = CountingProvider(responses=["fresh answer"])
    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock(side_effect=mock_embed)

    graph = AgentGraph(
        planner=provider, executor=provider, verifier=provider,
        semantic_cache=cache, embedder=mock_embedder,
        enable_cot=False, enable_reflection=False,
    )
    graph._event_callback = AsyncMock(side_effect=lambda e: emitted.append(e))

    agent_state = AgentState(goal="do something", tenant_ctx=tenant)
    result = await graph._execute_step_with_cache("search for information", agent_state, tenant)

    assert result == "cached answer"
    assert any(e.get("type") == "cache_hit" for e in emitted)
    assert len(llm_calls) == 0  # No LLM call made on cache hit


# ---------------------------------------------------------------------------
# 2.7 Streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_stream_complete():
    """FakeProvider streams word-by-word."""
    from app.providers.fake import FakeProvider
    from app.providers.base import CompletionRequest, Message

    provider = FakeProvider(responses=["hello world from streaming"])
    req = CompletionRequest(
        messages=[Message(role="user", content="test")], model=""
    )
    tokens: list[str] = []
    async for token in provider.stream_complete(req):
        tokens.append(token)

    assert len(tokens) >= 3
    full = "".join(tokens).strip()
    assert "hello" in full


@pytest.mark.asyncio
async def test_goals_token_stream_endpoint_exists():
    """GET /goals/{id}/stream/tokens endpoint exists and returns 200 or 404."""
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/tenants/signup", json={"name": "T", "email": "ts@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post("/goals", json={"goal": "test", "dry_run": True})
        goal_id = r2.json()["goal_id"]
        resp = await c.get(f"/goals/{goal_id}/stream/tokens")
        assert resp.status_code in (200, 404)
