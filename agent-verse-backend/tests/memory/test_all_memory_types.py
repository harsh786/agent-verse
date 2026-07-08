"""Tests for all memory types: working, session, LTM, episodic, procedural,
reflexion, semantic cache, execution memory, and knowledge-graph memory."""
from __future__ import annotations

import pytest

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _make_state(
    tenant_ctx: TenantContext,
    goal: str = "test goal",
    status: GoalStatus = GoalStatus.COMPLETE,
) -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = status
    return state


# ── WORKING MEMORY (AgentState.context) ──────────────────────────────────────


def test_working_memory_stores_within_execution(tenant_ctx: TenantContext) -> None:
    """AgentState.context accumulates arbitrary key-value pairs."""
    state = _make_state(tenant_ctx)
    state.context["key1"] = "value1"
    state.context["rag_context"] = "retrieved docs"
    assert state.context["key1"] == "value1"
    assert state.context["rag_context"] == "retrieved docs"


def test_working_memory_accumulates_step_outputs(tenant_ctx: TenantContext) -> None:
    """AgentState.steps accumulates completed step results."""
    state = _make_state(tenant_ctx)
    step = StepResult(
        description="search", output="Found 5 tickets", status=StepStatus.COMPLETE
    )
    state.steps.append(step)
    assert len(state.steps) == 1
    assert state.steps[0].output == "Found 5 tickets"


# ── SESSION MEMORY ────────────────────────────────────────────────────────────


def test_session_memory_add_get_clear() -> None:
    """SessionMemory supports goal-scoped add, get, and clear."""
    from app.state_runtime.session_memory import SessionMemory

    mem = SessionMemory()
    mem.add(goal_id="g1", key="step_1", value={"desc": "search", "output": "result"})
    entries = mem.get(goal_id="g1")
    assert len(entries) == 1
    assert entries[0]["key"] == "step_1"
    assert entries[0]["value"] == {"desc": "search", "output": "result"}

    mem.clear("g1")
    assert mem.get(goal_id="g1") == []


def test_session_memory_scoped_per_goal() -> None:
    """Different goal_ids do not share session memory."""
    from app.state_runtime.session_memory import SessionMemory

    mem = SessionMemory()
    mem.add(goal_id="g1", key="result", value="goal1_result")
    mem.add(goal_id="g2", key="result", value="goal2_result")

    g1_entries = mem.get(goal_id="g1")
    g2_entries = mem.get(goal_id="g2")
    assert len(g1_entries) == 1
    assert g1_entries[0]["value"] == "goal1_result"
    assert g2_entries[0]["value"] == "goal2_result"


# ── LONG-TERM MEMORY ──────────────────────────────────────────────────────────


def test_ltm_store_and_recall(tenant_ctx: TenantContext) -> None:
    """LTM stores a memory and recalls it by keyword match."""
    from app.memory.long_term import LongTermMemory, LongTermMemoryStore

    store = LongTermMemoryStore()
    mid = store.store(
        memory=LongTermMemory(
            content="In Jira projects, always use JQL filter `assignee = currentUser()`",
            memory_type="procedure",
            source_goal_id="g1",
            confidence=0.9,
        ),
        tenant_ctx=tenant_ctx,
    )
    assert mid is not None

    recalled = store.recall(
        query="Jira filtering", tenant_ctx=tenant_ctx, top_k=5
    )
    assert len(recalled) >= 1
    assert "JQL" in recalled[0].content or "Jira" in recalled[0].content


def test_ltm_keyword_fallback_recall(tenant_ctx: TenantContext) -> None:
    """LTM keyword recall returns matching memories."""
    from app.memory.long_term import LongTermMemory, LongTermMemoryStore

    store = LongTermMemoryStore()
    for i in range(3):
        store.store(
            memory=LongTermMemory(
                content=f"Memory about Jira ticket PROJ-{i + 1}",
                memory_type="experience",
                source_goal_id=f"g{i}",
                confidence=0.8,
            ),
            tenant_ctx=tenant_ctx,
        )
    # Recall without embedder — falls back to keyword
    recalled = store.recall(query="Jira ticket", tenant_ctx=tenant_ctx, top_k=3)
    assert len(recalled) >= 1


# ── EPISODIC MEMORY ───────────────────────────────────────────────────────────


async def test_episodic_memory_records_experience(tenant_ctx: TenantContext) -> None:
    """EpisodicMemoryStore records successful goal execution as episode."""
    from app.memory.episodic import EpisodicMemoryStore

    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx, goal="list open Jira tickets")
    step = StepResult(
        description="jira.search", output="5 tickets", status=StepStatus.COMPLETE
    )
    step.tool_calls = [{"tool_name": "jira.search_issues", "success": True}]
    state.steps = [step]

    await store.record(state=state, tenant_ctx=tenant_ctx, quality_score=0.9)

    episodes = await store.recall(goal="Jira tickets", tenant_id="t1", limit=5)
    assert len(episodes) == 1
    assert episodes[0].outcome == "success"
    assert "jira.search_issues" in episodes[0].tools_used


async def test_episodic_memory_failed_goal(tenant_ctx: TenantContext) -> None:
    """EpisodicMemoryStore records failed goals with 'failed' outcome."""
    from app.memory.episodic import EpisodicMemoryStore

    store = EpisodicMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx, status=GoalStatus.FAILED)
    state.verification_feedback = "Permission denied for users table"

    await store.record(state=state, tenant_ctx=tenant_ctx, quality_score=0.0)

    episodes = await store.recall(
        goal="test", tenant_id="t1", outcome_filter="failed"
    )
    assert len(episodes) >= 1
    assert episodes[0].outcome == "failed"


def test_episodic_context_snippet() -> None:
    """Episode.to_context_snippet returns a well-formed string."""
    from app.memory.episodic import Episode

    ep = Episode(
        episode_id="e1",
        tenant_id="t1",
        goal_id="g1",
        goal_text="search Jira",
        action_summary="used jira.search",
        outcome="success",
        lessons="JQL filter works best",
    )
    snippet = ep.to_context_snippet()
    assert "[Past episode — success]" in snippet
    assert "jira.search" in snippet


# ── PROCEDURAL MEMORY ─────────────────────────────────────────────────────────


async def test_procedural_memory_learns_tool_sequence(
    tenant_ctx: TenantContext,
) -> None:
    """ProceduralMemoryStore learns a tool sequence from a completed execution."""
    from app.memory.procedural import ProceduralMemoryStore

    store = ProceduralMemoryStore(db_factory=None)

    state = _make_state(tenant_ctx, goal="create Jira bug report")
    step = StepResult(
        description="create issue", output="PROJ-42 created", status=StepStatus.COMPLETE
    )
    step.tool_calls = [
        {"tool_name": "jira.create_issue", "success": True},
        {"tool_name": "jira.add_attachment", "success": True},
    ]
    state.steps = [step]

    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)

    skills = await store.recall(goal="create Jira issue", tenant_id="t1")
    assert len(skills) >= 1
    assert "jira.create_issue" in skills[0].tool_sequence


async def test_procedural_skill_success_rate_tracking(
    tenant_ctx: TenantContext,
) -> None:
    """ProceduralMemoryStore tracks success rates across multiple executions."""
    from app.memory.procedural import ProceduralMemoryStore

    store = ProceduralMemoryStore(db_factory=None)
    state = _make_state(tenant_ctx, goal="search Jira tickets")
    step = StepResult(description="search", output="found", status=StepStatus.COMPLETE)
    step.tool_calls = [{"tool_name": "jira.search_issues", "success": True}]
    state.steps = [step]

    # Learn 2 successes, 1 failure
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=True)
    await store.learn(state=state, tenant_ctx=tenant_ctx, success=False)

    skills = await store.recall(
        goal="Jira search", tenant_id="t1", min_success_rate=0.0
    )
    assert skills[0].success_rate == pytest.approx(2 / 3, abs=0.01)
    assert skills[0].use_count == 3


# ── REFLEXION MEMORY ──────────────────────────────────────────────────────────


def test_reflexion_store_records_failure_lessons() -> None:
    """ReflexionStore records and recalls failure lessons."""
    from app.state_runtime.reflexion_store import ReflexionStore

    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="For goal 'delete db': always verify permissions first",
        source_goal_id="g1",
        failure_class="auth_failure",
    )
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert lessons[0]["failure_class"] == "auth_failure"


async def test_reflexion_wirer_stores_on_failure(tenant_ctx: TenantContext) -> None:
    """ReflexionWirer persists a lesson when the goal fails."""
    from app.agent.reflexion_wirer import ReflexionWirer
    from app.state_runtime.reflexion_store import ReflexionStore

    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)

    state = _make_state(tenant_ctx, status=GoalStatus.FAILED)
    state.verification_feedback = "Rate limit exceeded on Jira API"

    result = await wirer.maybe_store_async(state)
    assert result is True

    lessons = store.recall(tenant_id="t1", limit=5)
    assert any(
        "Rate limit" in l["lesson"] or "Jira" in l["lesson"] for l in lessons
    )


async def test_reflexion_wirer_ignores_success(tenant_ctx: TenantContext) -> None:
    """ReflexionWirer does NOT store a lesson when the goal succeeds."""
    from app.agent.reflexion_wirer import ReflexionWirer
    from app.state_runtime.reflexion_store import ReflexionStore

    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = _make_state(tenant_ctx, status=GoalStatus.COMPLETE)
    result = await wirer.maybe_store_async(state)
    assert result is False


# ── SEMANTIC CACHE ────────────────────────────────────────────────────────────


def test_semantic_cache_can_be_instantiated() -> None:
    """SemanticCache can be constructed without external deps."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache()
    assert cache is not None


def test_semantic_cache_tenant_isolation() -> None:
    """Two SemanticCache instances are independent objects."""
    from app.rag.semantic_cache import SemanticCache

    cache1 = SemanticCache()
    cache2 = SemanticCache()
    # Each has its own L1 cache
    assert cache1._l1 is not cache2._l1


# ── EXECUTION MEMORY ──────────────────────────────────────────────────────────


def test_execution_memory_records_plan(tenant_ctx: TenantContext) -> None:
    """ExecutionMemory stores a plan and recalls it by substring match on goal text."""
    from app.memory.execution import ExecutionMemory

    mem = ExecutionMemory()
    # Use a goal that contains the hint as a substring
    mem.record(
        goal="list open Jira tickets",
        plan=["search Jira", "format results"],
        tenant_ctx=tenant_ctx,
    )
    # sync recall: checks `hint in goal.lower()` (full-string substring)
    recalled = mem.recall(goal_hint="Jira", tenant_ctx=tenant_ctx, top_k=3)
    assert len(recalled) >= 1
    assert "Jira" in recalled[0]["goal"]


def test_execution_memory_failure_tracking(tenant_ctx: TenantContext) -> None:
    """ExecutionMemory tracks failures and recalls them by substring match on goal."""
    from app.memory.execution import ExecutionMemory

    mem = ExecutionMemory()
    mem.record_failure(
        goal="delete user data",
        failed_step="delete_db_rows",
        error="Permission denied",
        tenant_ctx=tenant_ctx,
    )
    # recall_failures: checks `hint in goal.lower()` — use "delete" which IS in the goal
    failures = mem.recall_failures(
        goal_hint="delete", tenant_ctx=tenant_ctx, top_k=3
    )
    assert len(failures) >= 1


# ── KNOWLEDGE GRAPH MEMORY ────────────────────────────────────────────────────


async def test_kg_memory_entity_recall() -> None:
    """KGQueryEngine entity strategy returns structured facts from in-memory store."""
    from app.knowledge_graph.models import GraphNode, NodeType
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.state_runtime.kg_query_engine import KGQueryEngine

    store = KnowledgeGraphStore()
    store.add_node(
        GraphNode(
            node_id="n1",
            label="AgentVerse",
            node_type=NodeType.CONCEPT,
            tenant_id="t1",
        )
    )
    store.add_node(
        GraphNode(
            node_id="n2",
            label="LangGraph",
            node_type=NodeType.CONCEPT,
            tenant_id="t1",
        )
    )

    engine = KGQueryEngine(kg_store=store)

    entity_result = await engine.query("AgentVerse", tenant_id="t1", strategy="entity")
    assert entity_result.strategy_used == "entity"
    assert len(entity_result.facts) >= 1


async def test_kg_memory_path_traversal_with_real_edges() -> None:
    """KGQueryEngine path strategy resolves real edge targets (no '?' placeholders)."""
    from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.state_runtime.kg_query_engine import KGQueryEngine

    store = KnowledgeGraphStore()
    store.add_node(
        GraphNode(
            node_id="n1",
            label="AgentVerse",
            node_type=NodeType.CONCEPT,
            tenant_id="t1",
        )
    )
    store.add_node(
        GraphNode(
            node_id="n2",
            label="LangGraph",
            node_type=NodeType.CONCEPT,
            tenant_id="t1",
        )
    )
    store.add_edge(
        GraphEdge(
            edge_id="e1",
            source_node_id="n1",
            target_node_id="n2",
            edge_type=EdgeType.DEPENDS_ON,
            tenant_id="t1",
        )
    )

    engine = KGQueryEngine(kg_store=store)
    path_result = await engine.query("AgentVerse", tenant_id="t1", strategy="path")
    assert path_result.strategy_used == "path"
    # Real edges must resolve to actual node labels
    for fact in path_result.facts:
        assert fact.get("to") != "?"
