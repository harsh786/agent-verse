"""E2E tests for all agent patterns — unit + functional with FakeProvider."""
from __future__ import annotations

import pytest

from app.agent.state import GoalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


# ── REACT pattern ─────────────────────────────────────────────────────────────


def test_react_pattern_state() -> None:
    from app.agent.patterns.base import PatternState
    from app.agent.patterns.react import ReActPattern

    p = ReActPattern()
    assert p.state == PatternState.IMPLEMENTED
    assert p.node_name == "execute"


def test_react_pattern_is_compatible() -> None:
    from app.agent.pattern_config import GoalProperties
    from app.agent.patterns.react import ReActPattern

    p = ReActPattern()
    assert p.is_compatible(GoalProperties())


async def test_react_full_goal_execution(tenant_ctx: TenantContext) -> None:
    """Full ReAct loop: plan → execute → verify → complete."""
    from app.agent.graph import AgentGraph

    prov = FakeProvider(
        responses=[
            '{"steps": ["search for open tickets"]}',
            "Found 5 open tickets in PROJ",
            '{"success": true, "reason": "tickets found"}',
        ]
    )
    g = AgentGraph(planner=prov, executor=prov, verifier=prov)
    state = await g.run(goal="list open tickets", tenant_ctx=tenant_ctx)
    assert state.status == GoalStatus.COMPLETE
    assert state.iterations >= 1


# ── REFLECTION pattern ────────────────────────────────────────────────────────


async def test_reflection_activates_on_failure(tenant_ctx: TenantContext) -> None:
    """Reflection node fires when goal fails, then re-plans."""
    from app.agent.graph import AgentGraph

    responses = [
        '{"steps": ["call api"]}',
        "API call failed: 401 Unauthorized",
        '{"success": false, "reason": "auth failure", "retry": true}',
        "Reflected: need to check credentials first",
        '{"steps": ["check credentials", "retry call"]}',
        "Credentials valid, API returned data",
        "API retry succeeded",
        '{"success": true, "reason": "done"}',
    ]
    prov = FakeProvider(responses=responses)
    g = AgentGraph(
        planner=prov,
        executor=prov,
        verifier=prov,
        enable_reflection=True,
        max_iterations=4,
    )
    state = await g.run(goal="call external API", tenant_ctx=tenant_ctx)
    assert state.status == GoalStatus.COMPLETE


# ── SELF-REFINE pattern ───────────────────────────────────────────────────────


async def test_self_refine_improves_output(tenant_ctx: TenantContext) -> None:
    """SelfRefinePattern must improve output quality."""
    from app.agent.patterns.self_refine import SelfRefinePattern

    prov = FakeProvider(
        responses=[
            "A much more detailed and comprehensive answer with all the required information.",
        ]
    )
    pattern = SelfRefinePattern(max_iterations=1)
    result = await pattern.execute(
        last_output="A brief answer.",
        task="explain quantum computing in detail",
        provider=prov,
    )
    assert len(result) > len("A brief answer.")


def test_self_refine_node_in_graph() -> None:
    """Self-refine node must be compiled into graph when flag set."""
    from app.agent.graph import AgentGraph

    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_refine=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "refine" in node_names


async def test_self_refine_full_execution(tenant_ctx: TenantContext) -> None:
    """Self-refine in graph produces COMPLETE or FAILED status."""
    from app.agent.graph import AgentGraph

    prov = FakeProvider(
        responses=[
            '{"steps": ["write report"]}',
            "Initial report.",
            "Improved report with more detail.",
            '{"success": true, "reason": "done"}',
        ]
    )
    g = AgentGraph(
        planner=prov,
        executor=prov,
        verifier=prov,
        enable_self_refine=True,
    )
    state = await g.run(goal="write a report", tenant_ctx=tenant_ctx)
    assert state.status in (GoalStatus.COMPLETE, GoalStatus.FAILED)


# ── SELF-CONSISTENCY pattern ──────────────────────────────────────────────────


async def test_self_consistency_majority_vote(tenant_ctx: TenantContext) -> None:
    """Self-consistency must return most common answer."""
    from app.agent.patterns.self_consistency import SelfConsistencyPattern

    prov = FakeProvider(responses=["Paris", "Paris", "London"])
    pattern = SelfConsistencyPattern(n_samples=3)
    result = await pattern.execute(
        prompt="Capital of France?",
        provider=prov,
    )
    assert "Paris" in result


def test_self_consistency_node_in_graph() -> None:
    """self_consistency node compiled into graph when flag set."""
    from app.agent.graph import AgentGraph

    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_consistency=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "self_consistency" in node_names


# ── TREE OF THOUGHTS pattern ──────────────────────────────────────────────────


async def test_tree_of_thoughts_generates_answer(tenant_ctx: TenantContext) -> None:
    """TreeOfThoughtsPattern returns a non-empty string."""
    from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern

    prov = FakeProvider(
        responses=[
            "Thought 1: start small",
            "Thought 2: think big",
            "Thought 3: be systematic",
            '{"score": 0.9, "promising": true, "reason": "systematic"}',
            '{"score": 0.5, "promising": false, "reason": "vague"}',
            '{"score": 0.7, "promising": true, "reason": "good"}',
            "Final answer: use systematic approach with clear steps",
        ]
    )
    pattern = TreeOfThoughtsPattern(n_thoughts=3, max_depth=1)
    result = await pattern.execute(problem="How to solve complex problem?", provider=prov)
    assert isinstance(result, str)
    assert len(result) > 0


def test_tree_of_thoughts_node_in_graph() -> None:
    """tree_of_thoughts node compiled into graph when flag set."""
    from app.agent.graph import AgentGraph

    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_tree_of_thoughts=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "tree_of_thoughts" in node_names


# ── PEER REVIEW pattern ───────────────────────────────────────────────────────


async def test_peer_review_scores_output(tenant_ctx: TenantContext) -> None:
    """PeerReviewPattern evaluates output and returns a PeerReviewResult."""
    from app.agent.patterns.peer_review import PeerReviewPattern, PeerReviewResult

    prov = FakeProvider(
        responses=[
            '{"quality_score": 0.85, "critique": "Good comprehensive answer", "suggestions": [], "approved": true}'
        ]
    )
    pattern = PeerReviewPattern(quality_threshold=0.7)
    result = await pattern.execute(
        output="The capital of France is Paris, founded in 987 AD...",
        goal="What is the capital of France?",
        provider=prov,
    )
    assert isinstance(result, PeerReviewResult)
    assert result.quality_score == pytest.approx(0.85)
    assert result.approved is True


async def test_peer_review_rejects_low_quality(tenant_ctx: TenantContext) -> None:
    """PeerReviewPattern rejects low-quality output."""
    from app.agent.patterns.peer_review import PeerReviewPattern

    prov = FakeProvider(
        responses=[
            '{"quality_score": 0.2, "critique": "Too brief", "suggestions": ["Add more detail"], "approved": false}'
        ]
    )
    pattern = PeerReviewPattern()
    result = await pattern.execute(
        output="It's France.", goal="Explain French culture", provider=prov
    )
    assert result.approved is False
    assert result.quality_score < 0.5


def test_peer_review_node_in_graph() -> None:
    """peer_review node compiled into graph when flag set."""
    from app.agent.graph import AgentGraph

    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_peer_review=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "peer_review" in node_names


# ── REFLEXION pattern ─────────────────────────────────────────────────────────


async def test_reflexion_stores_and_recalls_lessons(tenant_ctx: TenantContext) -> None:
    """ReflexionPattern stores and recalls failure lessons."""
    from app.agent.patterns.reflexion import ReflexionPattern
    from app.state_runtime.reflexion_store import ReflexionStore

    store = ReflexionStore()
    pattern = ReflexionPattern(reflexion_store=store)

    await pattern.store_lesson(
        tenant_id="t1",
        goal="delete prod db",
        feedback="permission denied — always verify permissions first",
        source_goal_id="g1",
        failure_class="auth_failure",
    )

    lessons = pattern.recall_lessons(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert "permission" in lessons[0]["lesson"]


def test_reflexion_formats_for_context() -> None:
    """ReflexionPattern formats lessons as a prompt context block."""
    from app.agent.patterns.reflexion import ReflexionPattern
    from app.state_runtime.reflexion_store import ReflexionStore

    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="Lesson 1",
        source_goal_id="g1",
        failure_class="error",
    )
    pattern = ReflexionPattern(reflexion_store=store)
    ctx = pattern.format_for_context(pattern.recall_lessons(tenant_id="t1"))
    assert "Lesson 1" in ctx
    assert "error" in ctx.lower() or "failure" in ctx.lower()


# ── GOAL-TREE pattern ─────────────────────────────────────────────────────────


def test_goal_tree_pattern_registered() -> None:
    """GoalTreePattern is registered and implemented."""
    from app.agent.patterns.base import PatternState
    from app.agent.patterns.goal_tree import GoalTreePattern

    p = GoalTreePattern()
    assert p.pattern_id == "goal_tree"
    assert p.state in (PatternState.IMPLEMENTED, PatternState.PARTIAL)


# ── SUPERVISOR pattern ────────────────────────────────────────────────────────


def test_supervisor_pattern_registered() -> None:
    """SupervisorPattern has correct pattern_id."""
    from app.agent.patterns.supervisor import SupervisorPattern

    p = SupervisorPattern()
    assert p.pattern_id == "supervisor"


# ── DEBATE pattern ────────────────────────────────────────────────────────────


def test_debate_pattern_registered() -> None:
    """DebatePattern has correct pattern_id."""
    from app.agent.patterns.debate import DebatePattern

    p = DebatePattern()
    assert p.pattern_id == "debate"


# ── Dynamic pattern assembly ──────────────────────────────────────────────────


def test_pattern_assembler_selects_patterns_for_expert_goal() -> None:
    """Expert + analytical goal should include react + higher-level reasoning patterns."""
    from app.agent.pattern_assembler import PatternAssembler
    from app.agent.pattern_config import Complexity, Domain, GoalProperties

    assembler = PatternAssembler()
    props = GoalProperties(complexity=Complexity.EXPERT, domain=Domain.ANALYTICAL)
    config = assembler.assemble(props, {})
    assert len(config.reasoning_patterns) >= 1
    assert "react" in config.reasoning_patterns


def test_pattern_assembler_safety_rules_always_apply() -> None:
    """Critical risk goals always get guardrails and HITL."""
    from app.agent.pattern_assembler import PatternAssembler
    from app.agent.pattern_config import GoalProperties, RiskLevel

    assembler = PatternAssembler()
    props = GoalProperties(risk=RiskLevel.CRITICAL)
    config = assembler.assemble(props, {})
    assert "guardrails" in config.safety_patterns
    assert "hitl" in config.safety_patterns


def test_dynamic_graph_assembler_translates_all_patterns() -> None:
    """DynamicGraphAssembler creates graph with all requested pattern nodes."""
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig

    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(
        reasoning_patterns=[
            "react",
            "self_refine",
            "self_consistency",
            "peer_review",
            "tree_of_thoughts",
        ],
    )
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "refine" in node_names
    assert "self_consistency" in node_names
    assert "peer_review" in node_names
    assert "tree_of_thoughts" in node_names
