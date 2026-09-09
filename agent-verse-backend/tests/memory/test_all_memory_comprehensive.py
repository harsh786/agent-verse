"""Comprehensive memory tests (25+ tests).

Tests EpisodicMemoryStore, ProceduralMemoryStore, LongTermMemoryStore,
ExecutionMemory, ReflexionStore, and SemanticCache — all with mocked DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def _tenant_ctx(tenant_id: str = "tenant1"):  # type: ignore[return]
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="test-key")


# ─────────────────────────────────────────────────────────────────────────────
# EpisodicMemoryStore
# ─────────────────────────────────────────────────────────────────────────────

class TestEpisodicMemoryStore:
    def test_init_no_db(self) -> None:
        from app.memory.episodic import EpisodicMemoryStore
        store = EpisodicMemoryStore()
        assert store._db is None
        assert store._cache == {}

    async def test_recall_empty_returns_empty_list(self) -> None:
        from app.memory.episodic import EpisodicMemoryStore
        store = EpisodicMemoryStore()
        result = await store.recall(goal="test goal", tenant_id="t1")
        assert result == []

    async def test_recall_in_memory_keyword_match(self) -> None:
        from app.memory.episodic import Episode, EpisodicMemoryStore
        store = EpisodicMemoryStore()
        # Seed in-memory cache directly
        store._cache["t1"] = [
            Episode(
                episode_id="e1",
                tenant_id="t1",
                goal_id="g1",
                goal_text="build a machine learning pipeline",
                action_summary="ran training",
                outcome="success",
                lessons="use checkpoints",
                quality_score=0.9,
            ),
            Episode(
                episode_id="e2",
                tenant_id="t1",
                goal_id="g2",
                goal_text="cook pasta recipe",
                action_summary="cooked dinner",
                outcome="success",
                lessons="add salt",
                quality_score=0.8,
            ),
        ]
        result = await store.recall(goal="machine learning training", tenant_id="t1", limit=1)
        assert len(result) >= 1
        assert result[0].goal_id == "g1"

    async def test_recall_with_outcome_filter(self) -> None:
        from app.memory.episodic import Episode, EpisodicMemoryStore
        store = EpisodicMemoryStore()
        store._cache["t1"] = [
            Episode("e1", "t1", "g1", "run tests", "ran", "failed", "fix imports"),
            Episode("e2", "t1", "g2", "deploy app", "deployed", "success", "works"),
        ]
        result = await store.recall(goal="run", tenant_id="t1", outcome_filter="success")
        assert all(e.outcome == "success" for e in result)

    async def test_recall_limit(self) -> None:
        from app.memory.episodic import Episode, EpisodicMemoryStore
        store = EpisodicMemoryStore()
        store._cache["t1"] = [
            Episode(f"e{i}", "t1", f"g{i}", "run tests", "ran", "success", "", 0.9)
            for i in range(10)
        ]
        result = await store.recall(goal="run tests", tenant_id="t1", limit=3)
        assert len(result) <= 3

    def test_format_for_context_empty(self) -> None:
        from app.memory.episodic import EpisodicMemoryStore
        store = EpisodicMemoryStore()
        assert store.format_for_context([]) == ""

    def test_format_for_context_with_episodes(self) -> None:
        from app.memory.episodic import Episode, EpisodicMemoryStore
        store = EpisodicMemoryStore()
        ep = Episode("e1", "t1", "g1", "build thing", "did stuff", "success", "lesson1")
        result = store.format_for_context([ep])
        assert "Episodic memory" in result
        assert "build thing" in result

    def test_episode_to_context_snippet(self) -> None:
        from app.memory.episodic import Episode
        ep = Episode("e1", "t1", "g1", "deploy app", "ran steps", "success", "Use blue/green")
        snippet = ep.to_context_snippet()
        assert "deploy app" in snippet
        assert "success" in snippet

    async def test_recall_different_tenants_isolated(self) -> None:
        from app.memory.episodic import Episode, EpisodicMemoryStore
        store = EpisodicMemoryStore()
        store._cache["t1"] = [Episode("e1", "t1", "g1", "task A", "run", "success", "")]
        store._cache["t2"] = []
        result = await store.recall(goal="task A", tenant_id="t2")
        assert result == []
# ─────────────────────────────────────────────────────────────────────────────
# ProceduralMemoryStore
# ─────────────────────────────────────────────────────────────────────────────

class TestProceduralMemoryStore:
    def test_init_no_db(self) -> None:
        from app.memory.procedural import ProceduralMemoryStore
        store = ProceduralMemoryStore()
        assert store._db is None

    async def test_recall_empty_returns_empty(self) -> None:
        from app.memory.procedural import ProceduralMemoryStore
        store = ProceduralMemoryStore()
        result = await store.recall(goal="do something", tenant_id="t1")
        assert result == []

    async def test_learn_adds_skill_to_cache(self) -> None:
        from app.agent.state import AgentState, GoalStatus, StepResult
        from app.memory.procedural import ProceduralMemoryStore
        from app.tenancy.context import PlanTier, TenantContext
        store = ProceduralMemoryStore()
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="test-key")
        state = AgentState(goal="Fix JIRA-123 bug", goal_id="g1", tenant_ctx=ctx)
        state.status = GoalStatus.COMPLETE
        state.steps = [
            StepResult(
                description="step1",
                tool_calls=[{"tool_name": "jira_search"}, {"tool_name": "github_pr"}],
            )
        ]
        await store.learn(state=state, tenant_ctx=ctx, success=True)
        result = await store.recall(goal="Fix JIRA bug", tenant_id="t1")
        assert len(result) >= 1
        assert result[0].domain == "jira"

    async def test_learn_no_tools_skips(self) -> None:
        from app.agent.state import AgentState, GoalStatus
        from app.memory.procedural import ProceduralMemoryStore
        from app.tenancy.context import PlanTier, TenantContext
        store = ProceduralMemoryStore()
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="test-key")
        state = AgentState(goal="Do nothing with tools", goal_id="g1", tenant_ctx=ctx)
        state.status = GoalStatus.COMPLETE
        await store.learn(state=state, tenant_ctx=ctx)
        result = await store.recall(goal="nothing", tenant_id="t1")
        assert result == []

    async def test_recall_with_min_success_rate(self) -> None:
        from app.memory.procedural import ProceduralMemoryStore, Skill
        store = ProceduralMemoryStore()
        store._cache["t1"] = [
            Skill("s1", "t1", "run tests", "git", ["pytest"], use_count=3, success_rate=0.9),
            Skill("s2", "t1", "deploy app", "git", ["deploy"], use_count=2, success_rate=0.3),
        ]
        result = await store.recall(goal="tests", tenant_id="t1", min_success_rate=0.7)
        assert all(s.success_rate >= 0.7 for s in result)

    def test_skill_to_hint(self) -> None:
        from app.memory.procedural import Skill
        skill = Skill("s1", "t1", "Deploy the app", "git", ["build", "push", "deploy"])
        hint = skill.to_hint()
        assert "Deploy" in hint
        assert "build" in hint

    def test_format_for_context_empty(self) -> None:
        from app.memory.procedural import ProceduralMemoryStore
        store = ProceduralMemoryStore()
        assert store.format_for_context([]) == ""

    def test_format_for_context_with_skills(self) -> None:
        from app.memory.procedural import ProceduralMemoryStore, Skill
        store = ProceduralMemoryStore()
        skills = [Skill("s1", "t1", "Build pipeline", "git", ["tool1"])]
        result = store.format_for_context(skills)
        assert "Procedural memory" in result


# ─────────────────────────────────────────────────────────────────────────────
# LongTermMemoryStore
# ─────────────────────────────────────────────────────────────────────────────

class TestLongTermMemoryStore:
    def test_init(self) -> None:
        from app.memory.long_term import LongTermMemoryStore
        store = LongTermMemoryStore()
        assert store._memories == {}

    def test_store_and_recall(self) -> None:
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        memory = LongTermMemory(
            content="Python is great for ML",
            source_goal_id="g1",
            memory_type="domain_fact",
            tags=["python", "ml"],
        )
        mid = store.store(memory=memory, tenant_ctx=ctx)
        assert mid == memory.memory_id
        results = store.recall(query="python machine learning", tenant_ctx=ctx, top_k=5)
        assert len(results) >= 1
        assert results[0].content == "Python is great for ML"

    def test_recall_empty(self) -> None:
        from app.memory.long_term import LongTermMemoryStore
        store = LongTermMemoryStore()
        result = store.recall(query="anything", tenant_ctx=_tenant_ctx("tenant1"), top_k=5)
        assert result == []

    def test_recall_with_memory_type_filter(self) -> None:
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        store.store(
            memory=LongTermMemory("Tool tip", "g1", "tool_preference"), tenant_ctx=ctx
        )
        store.store(
            memory=LongTermMemory("Domain knowledge", "g2", "domain_fact"), tenant_ctx=ctx
        )
        results = store.recall(query="tip", tenant_ctx=ctx, memory_type="tool_preference")
        assert all(m.memory_type == "tool_preference" for m in results)

    def test_delete_memory(self) -> None:
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        mem = LongTermMemory("content", "g1", "domain_fact")
        store.store(memory=mem, tenant_ctx=ctx)
        deleted = store.delete(memory_id=mem.memory_id, tenant_ctx=ctx)
        assert deleted is True
        assert store.recall(query="content", tenant_ctx=ctx) == []

    def test_delete_nonexistent_returns_false(self) -> None:
        from app.memory.long_term import LongTermMemoryStore
        store = LongTermMemoryStore()
        assert store.delete(memory_id="nonexistent", tenant_ctx=_tenant_ctx("tenant1")) is False

    def test_list_all(self) -> None:
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        for i in range(3):
            store.store(memory=LongTermMemory(f"fact {i}", "g1", "domain_fact"), tenant_ctx=ctx)
        all_mems = store.list_all(tenant_ctx=ctx)
        assert len(all_mems) == 3

    def test_extract_from_goal(self) -> None:
        from app.memory.long_term import LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        ids = store.extract_from_goal(
            goal="Deploy app to production",
            result="Deployment successful",
            goal_id="g1",
            tenant_ctx=ctx,
        )
        assert len(ids) == 1
        all_mems = store.list_all(tenant_ctx=ctx)
        assert "Deploy app" in all_mems[0].content

    async def test_recall_async_falls_back_to_memory(self) -> None:
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        mem = LongTermMemory("Python tip", "g1", "tool_preference")
        store.store(memory=mem, tenant_ctx=ctx)
        result = await store.recall_async("Python", ctx, top_k=5)
        assert len(result) >= 1

    async def test_store_async_no_db(self) -> None:
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        store = LongTermMemoryStore()
        ctx = _tenant_ctx("tenant1")
        mem = LongTermMemory("Async test content", "g1", "domain_fact")
        mid = await store.store_async(memory=mem, tenant_ctx=ctx, db=None)
        assert mid == mem.memory_id


# ─────────────────────────────────────────────────────────────────────────────
# ExecutionMemory
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionMemory:
    def test_init(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        assert mem._plans == {}

    def test_record_and_recall(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        ctx = _tenant_ctx("tenant1")
        mem.record(goal="Build a REST API", plan=["step1", "step2"], tenant_ctx=ctx)
        results = mem.recall(goal_hint="REST API", tenant_ctx=ctx)
        assert len(results) >= 1
        assert results[0]["goal"] == "Build a REST API"

    def test_recall_no_match(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        ctx = _tenant_ctx("tenant1")
        mem.record(goal="Cook pasta", plan=["boil water"], tenant_ctx=ctx)
        results = mem.recall(goal_hint="deploy kubernetes", tenant_ctx=ctx)
        assert results == []

    def test_record_failure_and_recall_failures(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        ctx = _tenant_ctx("tenant1")
        mem.record_failure(
            goal="Deploy app",
            failed_step="run migration",
            error="DB connection error",
            tenant_ctx=ctx,
        )
        results = mem.recall_failures(goal_hint="Deploy", tenant_ctx=ctx)
        assert len(results) >= 1
        assert results[0]["error"] == "DB connection error"

    def test_top_k_limits_results(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        ctx = _tenant_ctx("tenant1")
        for i in range(10):
            mem.record(goal=f"build thing {i}", plan=[f"step{i}"], tenant_ctx=ctx)
        results = mem.recall(goal_hint="build", tenant_ctx=ctx, top_k=3)
        assert len(results) <= 3

    async def test_record_async_no_db(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        await mem.record_async(
            goal="Build pipeline",
            plan=["step1", "step2"],
            success=True,
            tenant_id="tenant1",
            db=None,
        )
        # Should be in _plans after successful async record
        assert len(mem._plans.get("tenant1", [])) >= 1

    async def test_record_failure_async_no_db(self) -> None:
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        await mem.record_failure_async(
            goal="Deploy to prod",
            error="Permission denied",
            tenant_id="tenant1",
            db=None,
        )
        assert len(mem._failures.get("tenant1", [])) == 1


# ─────────────────────────────────────────────────────────────────────────────
# ReflexionStore
# ─────────────────────────────────────────────────────────────────────────────

class TestReflexionStore:
    def test_init(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()
        assert store._lessons == {}

    def test_record_and_recall(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()
        store.record(
            tenant_id="t1",
            lesson="Always check env vars first",
            source_goal_id="g1",
            failure_class="config_error",
        )
        lessons = store.recall(tenant_id="t1", limit=10)
        assert len(lessons) == 1
        assert lessons[0]["lesson"] == "Always check env vars first"
        assert lessons[0]["failure_class"] == "config_error"

    def test_recall_empty(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()
        assert store.recall(tenant_id="t1") == []

    def test_recall_limit(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore(max_per_tenant=50)
        for i in range(20):
            store.record(
                tenant_id="t1",
                lesson=f"Lesson {i}",
                source_goal_id=f"g{i}",
                failure_class="error",
            )
        result = store.recall(tenant_id="t1", limit=5)
        assert len(result) == 5

    async def test_record_async_no_db(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()
        await store.record_async(
            tenant_id="t1",
            lesson="async lesson",
            source_goal_id="g1",
            failure_class="timeout",
            db_factory=None,
        )
        assert len(store.recall(tenant_id="t1")) == 1

    async def test_load_from_db_none_factory(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()
        # Should not raise
        await store.load_from_db(tenant_id="t1", db_factory=None)

    async def test_load_from_db_with_mock(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(
                fetchall=MagicMock(
                    return_value=[
                        ("t1", "Lesson from DB", "g1", "tool_error"),
                    ]
                )
            )
        )
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_db_factory = MagicMock(return_value=mock_ctx)

        await store.load_from_db(tenant_id="t1", db_factory=mock_db_factory)
        lessons = store.recall(tenant_id="t1")
        assert len(lessons) >= 1

    def test_max_per_tenant_enforced(self) -> None:
        from app.state_runtime.reflexion_store import ReflexionStore
        store = ReflexionStore(max_per_tenant=3)
        for i in range(10):
            store.record(
                tenant_id="t1", lesson=f"lesson{i}", source_goal_id=f"g{i}", failure_class="e"
            )
        lessons = store.recall(tenant_id="t1", limit=100)
        assert len(lessons) <= 3
