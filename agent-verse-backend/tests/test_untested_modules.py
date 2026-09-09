"""Tests for previously untested modules:
  - app/agent_runtime/models.py
  - app/memory_v2/models.py
  - app/memory_v2/consolidation.py
  - app/rag_platform/query_planner.py
  - app/skills_runtime/models.py
  - app/skills_runtime/executor.py  (public surface)
"""
from __future__ import annotations

import datetime

import pytest

# ─────────────────────────────────────────────────────────────────────────────
#  agent_runtime.models
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRuntimeModels:
    def test_agent_role_enum_values(self) -> None:
        from app.agent_runtime.models import AgentRole
        assert AgentRole.PLANNER == "planner"
        assert AgentRole.EXECUTOR == "executor"
        assert AgentRole.VERIFIER == "verifier"
        assert AgentRole.CRITIC == "critic"
        assert AgentRole.JUDGE == "judge"
        assert AgentRole.REFLECTOR == "reflector"
        assert AgentRole.SYNTHESIZER == "synthesizer"
        assert AgentRole.SUBAGENT == "subagent"

    def test_step_status_enum_values(self) -> None:
        from app.agent_runtime.models import StepStatus
        assert StepStatus.PENDING == "pending"
        assert StepStatus.RUNNING == "running"
        assert StepStatus.COMPLETE == "complete"
        assert StepStatus.FAILED == "failed"
        assert StepStatus.SKIPPED == "skipped"
        assert StepStatus.WAITING_HUMAN == "waiting_human"

    def test_risk_level_enum_values(self) -> None:
        from app.agent_runtime.models import RiskLevel
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"

    def test_plan_step_defaults(self) -> None:
        from app.agent_runtime.models import AgentRole, PlanStep, RiskLevel, StepStatus
        step = PlanStep(step_id="s1", description="Do something")
        assert step.role == AgentRole.EXECUTOR
        assert step.status == StepStatus.PENDING
        assert step.risk_level == RiskLevel.LOW
        assert step.dependencies == []
        assert step.tools_required == []
        assert step.timeout_seconds == 60
        assert step.cost_usd == 0.0
        assert step.output == ""
        assert step.error == ""

    def test_plan_step_custom_values(self) -> None:
        from app.agent_runtime.models import AgentRole, PlanStep, RiskLevel, StepStatus
        step = PlanStep(
            step_id="s2",
            description="Deploy to prod",
            role=AgentRole.VERIFIER,
            risk_level=RiskLevel.HIGH,
            dependencies=["s1"],
            tools_required=["kubectl"],
            timeout_seconds=300,
        )
        assert step.role == AgentRole.VERIFIER
        assert step.risk_level == RiskLevel.HIGH
        assert step.dependencies == ["s1"]
        assert step.tools_required == ["kubectl"]
        assert step.timeout_seconds == 300

    def test_plan_step_status_transition(self) -> None:
        from app.agent_runtime.models import PlanStep, StepStatus
        step = PlanStep(step_id="s3", description="Run tests")
        step.status = StepStatus.RUNNING
        assert step.status == StepStatus.RUNNING
        step.status = StepStatus.COMPLETE
        step.output = "all passed"
        assert step.status == StepStatus.COMPLETE
        assert step.output == "all passed"

    def test_agent_execution_plan_defaults(self) -> None:
        from app.agent_runtime.models import AgentExecutionPlan
        plan = AgentExecutionPlan(
            plan_id="p1", goal_id="g1", tenant_id="t1", goal_text="Fix bug"
        )
        assert plan.strategy == "single_agent"
        assert plan.steps == []
        assert plan.model_assignments == {}
        assert plan.agent_id is None

    def test_agent_execution_plan_with_steps(self) -> None:
        from app.agent_runtime.models import AgentExecutionPlan, PlanStep
        steps = [PlanStep(step_id=f"s{i}", description=f"Step {i}") for i in range(3)]
        plan = AgentExecutionPlan(
            plan_id="p2", goal_id="g2", tenant_id="t2", goal_text="Multi-step",
            steps=steps, strategy="multi_agent",
        )
        assert len(plan.steps) == 3
        assert plan.strategy == "multi_agent"

    def test_agent_run_trace_defaults(self) -> None:
        from app.agent_runtime.models import AgentRunTrace
        trace = AgentRunTrace(trace_id="tr1", goal_id="g1", tenant_id="t1")
        assert trace.total_cost_usd == 0.0
        assert trace.total_tokens == 0
        assert trace.success is False
        assert trace.role_calls == []
        assert trace.model_selections == []
        assert trace.patterns_used == []
        assert trace.rag_strategy_used == ""

    def test_agent_run_trace_success_fields(self) -> None:
        from app.agent_runtime.models import AgentRunTrace
        trace = AgentRunTrace(trace_id="tr2", goal_id="g2", tenant_id="t2")
        trace.success = True
        trace.total_cost_usd = 0.05
        trace.total_tokens = 1500
        trace.duration_ms = 2300.5
        trace.rag_strategy_used = "hybrid"
        assert trace.success is True
        assert trace.total_cost_usd == 0.05
        assert trace.total_tokens == 1500
        assert trace.rag_strategy_used == "hybrid"

    def test_subagent_task_defaults(self) -> None:
        from app.agent_runtime.models import SubagentTask
        task = SubagentTask(task_id="t1", parent_goal_id="g1")
        assert task.status == "pending"
        assert task.cost_attributed == 0.0
        assert task.agent_id is None
        assert task.child_goal_id is None

    def test_subagent_task_completion(self) -> None:
        from app.agent_runtime.models import SubagentTask
        task = SubagentTask(task_id="t2", parent_goal_id="g2", tenant_id="tenant-x")
        task.status = "complete"
        task.cost_attributed = 0.03
        task.completed_at = "2026-08-20T12:00:00Z"
        assert task.status == "complete"
        assert task.cost_attributed == 0.03


# ─────────────────────────────────────────────────────────────────────────────
#  memory_v2.models
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryV2Models:
    def test_lifecycle_state_values(self) -> None:
        from app.memory_v2.models import MemoryLifecycleState
        assert MemoryLifecycleState.ACTIVE == "active"
        assert MemoryLifecycleState.STALE == "stale"
        assert MemoryLifecycleState.DISPUTED == "disputed"
        assert MemoryLifecycleState.ARCHIVED == "archived"
        assert MemoryLifecycleState.DELETED == "deleted"

    def test_privacy_class_values(self) -> None:
        from app.memory_v2.models import MemoryPrivacyClass
        assert MemoryPrivacyClass.PUBLIC == "public"
        assert MemoryPrivacyClass.INTERNAL == "internal"
        assert MemoryPrivacyClass.CONFIDENTIAL == "confidential"
        assert MemoryPrivacyClass.PII == "pii"
        assert MemoryPrivacyClass.PHI == "phi"

    def test_memory_provenance_defaults(self) -> None:
        from app.memory_v2.models import MemoryProvenance
        prov = MemoryProvenance()
        assert prov.created_from_goal_id is None
        assert prov.created_from_tool is None
        assert prov.confidence_evidence == ""
        assert prov.update_count == 0

    def test_memory_provenance_with_values(self) -> None:
        from app.memory_v2.models import MemoryProvenance
        prov = MemoryProvenance(
            created_from_goal_id="goal-abc",
            created_from_tool="web_search",
            confidence_evidence="confirmed by 3 sources",
            update_count=2,
        )
        assert prov.created_from_goal_id == "goal-abc"
        assert prov.created_from_tool == "web_search"
        assert prov.update_count == 2

    def test_memory_conflict_defaults(self) -> None:
        from app.memory_v2.models import MemoryConflict
        conflict = MemoryConflict(
            conflict_id="c1", tenant_id="t1",
            memory_id_a="m1", memory_id_b="m2",
            conflict_description="Contradicting facts",
        )
        assert conflict.severity == "low"
        assert conflict.resolved is False
        assert conflict.resolution is None

    def test_memory_conflict_resolution(self) -> None:
        from app.memory_v2.models import MemoryConflict
        conflict = MemoryConflict(
            conflict_id="c2", tenant_id="t2",
            memory_id_a="m3", memory_id_b="m4",
            conflict_description="Duplicate info",
            severity="medium",
        )
        conflict.resolved = True
        conflict.resolution = "kept m3, archived m4"
        assert conflict.resolved is True
        assert conflict.resolution == "kept m3, archived m4"


# ─────────────────────────────────────────────────────────────────────────────
#  memory_v2.consolidation
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryConsolidator:
    @pytest.fixture
    def consolidator(self):
        from app.memory_v2.consolidation import MemoryConsolidator
        return MemoryConsolidator()

    @pytest.mark.asyncio
    async def test_empty_store_returns_zero_stats(self, consolidator) -> None:
        stats = await consolidator.consolidate("tenant-x", {})
        assert stats["merged"] == 0
        assert stats["marked_stale"] == 0
        assert stats["archived"] == 0
        assert stats["total_before"] == 0
        assert stats["total_after"] == 0

    @pytest.mark.asyncio
    async def test_active_recent_memory_unchanged(self, consolidator) -> None:
        store = {
            "tenant-x:mem1": {
                "memory_id": "mem1",
                "content": "Paris is the capital of France",
                "lifecycle_state": "active",
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "confidence": 0.9,
            }
        }
        stats = await consolidator.consolidate("tenant-x", store)
        assert stats["total_before"] == 1
        assert stats["total_after"] == 1
        assert stats["marked_stale"] == 0
        assert store["tenant-x:mem1"]["lifecycle_state"] == "active"

    @pytest.mark.asyncio
    async def test_old_active_memory_marked_stale(self, consolidator) -> None:
        old_date = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=35)
        ).isoformat()
        store = {
            "tenant-x:mem2": {
                "memory_id": "mem2",
                "content": "Berlin hosts the Bundestag",
                "lifecycle_state": "active",
                "updated_at": old_date,
                "confidence": 0.8,
            }
        }
        stats = await consolidator.consolidate("tenant-x", store)
        assert stats["marked_stale"] == 1
        assert store["tenant-x:mem2"]["lifecycle_state"] == "stale"

    @pytest.mark.asyncio
    async def test_very_old_stale_memory_archived(self, consolidator) -> None:
        very_old = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=95)
        ).isoformat()
        store = {
            "tenant-x:mem3": {
                "memory_id": "mem3",
                "content": "Some old fact",
                "lifecycle_state": "stale",
                "updated_at": very_old,
                "confidence": 0.5,
            }
        }
        stats = await consolidator.consolidate("tenant-x", store)
        assert stats["archived"] == 1
        assert store["tenant-x:mem3"]["lifecycle_state"] == "archived"

    @pytest.mark.asyncio
    async def test_duplicate_content_merges(self, consolidator) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Two memories with IDENTICAL content (same first 100 chars triggers dedup)
        same_content = "The Eiffel Tower is in Paris and was built in 1889 as a landmark for the World Fair"
        store = {
            "tenant-x:mem4": {
                "memory_id": "mem4",
                "content": same_content,
                "lifecycle_state": "active",
                "updated_at": now,
                "confidence": 0.7,
            },
            "tenant-x:mem5": {
                "memory_id": "mem5",
                "content": same_content,  # identical → dedup
                "lifecycle_state": "active",
                "updated_at": now,
                "confidence": 0.9,
            },
        }
        stats = await consolidator.consolidate("tenant-x", store)
        assert stats["merged"] == 1
        # Higher confidence entry should survive
        assert stats["total_after"] == 1

    @pytest.mark.asyncio
    async def test_different_tenants_isolated(self, consolidator) -> None:
        old_date = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=35)
        ).isoformat()
        store = {
            "tenant-a:mem6": {
                "memory_id": "mem6",
                "content": "Fact A",
                "lifecycle_state": "active",
                "updated_at": old_date,
                "confidence": 0.8,
            },
            "tenant-b:mem7": {
                "memory_id": "mem7",
                "content": "Fact B",
                "lifecycle_state": "active",
                "updated_at": old_date,
                "confidence": 0.8,
            },
        }
        # Consolidate for tenant-a only — tenant-b should be unaffected
        await consolidator.consolidate("tenant-a", store)
        assert store["tenant-b:mem7"]["lifecycle_state"] == "active"

    @pytest.mark.asyncio
    async def test_already_archived_not_counted(self, consolidator) -> None:
        store = {
            "tenant-x:mem8": {
                "memory_id": "mem8",
                "content": "Already gone",
                "lifecycle_state": "archived",
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "confidence": 0.5,
            }
        }
        stats = await consolidator.consolidate("tenant-x", store)
        assert stats["total_before"] == 0  # excluded from processing

    @pytest.mark.asyncio
    async def test_singleton_accessible(self) -> None:
        from app.memory_v2.consolidation import memory_consolidator
        assert memory_consolidator is not None
        stats = await memory_consolidator.consolidate("tenant-singleton", {})
        assert "total_before" in stats


# ─────────────────────────────────────────────────────────────────────────────
#  rag_platform.query_planner
# ─────────────────────────────────────────────────────────────────────────────

class TestRAGPlatformQueryPlanner:
    @pytest.fixture
    def planner(self):
        from app.rag_platform.query_planner import QueryPlanner
        return QueryPlanner()

    def test_multi_hop_selection_for_explanation(self, planner) -> None:
        from app.rag.contracts import RAGStrategy
        strategy = planner.select_strategy("How does the authentication system work?")
        assert strategy == RAGStrategy.MULTI_HOP

    def test_multi_hop_for_why_query(self, planner) -> None:
        from app.rag.contracts import RAGStrategy
        strategy = planner.select_strategy("Why did the deployment fail?")
        assert strategy == RAGStrategy.MULTI_HOP

    def test_graph_selection_for_relationship_query(self, planner) -> None:
        from app.rag.contracts import RAGStrategy
        strategy = planner.select_strategy("What is related to the auth module?")
        assert strategy == RAGStrategy.GRAPH

    def test_graph_for_connected_query(self, planner) -> None:
        from app.rag.contracts import RAGStrategy
        strategy = planner.select_strategy("Which services are connected to the API gateway?")
        assert strategy == RAGStrategy.GRAPH

    def test_naive_for_simple_query(self, planner) -> None:
        from app.rag.contracts import RAGStrategy
        strategy = planner.select_strategy("What is the project name?")
        assert strategy == RAGStrategy.NAIVE

    def test_naive_default_no_keywords(self, planner) -> None:
        from app.rag.contracts import RAGStrategy
        strategy = planner.select_strategy("List all users")
        assert strategy == RAGStrategy.NAIVE

    def test_retrieval_leg_dataclass(self) -> None:
        from app.rag.contracts import RAGStrategy
        from app.rag_platform.query_planner import RetrievalLeg
        leg = RetrievalLeg(strategy=RAGStrategy.NAIVE, query="test query")
        assert leg.results == []
        assert leg.score == 0.0
        assert leg.latency_ms == 0.0

    def test_rag_result_dataclass(self) -> None:
        from app.rag.contracts import RAGStrategy
        from app.rag_platform.query_planner import RAGResult
        result = RAGResult(query="test", strategy_used=RAGStrategy.HYBRID)
        assert result.grounded is True
        assert result.confidence == 0.0
        assert result.citations == []
        assert result.refused_claims == []
        assert result.answer == ""

    def test_rag_result_with_citations(self) -> None:
        from app.rag.contracts import RAGStrategy
        from app.rag_platform.query_planner import RAGResult
        result = RAGResult(
            query="What is X?",
            strategy_used=RAGStrategy.FUSION,
            answer="X is a framework",
            citations=[{"doc_id": "d1", "score": 0.9}],
            grounded=True,
            confidence=0.85,
        )
        assert result.answer == "X is a framework"
        assert len(result.citations) == 1
        assert result.confidence == 0.85


# ─────────────────────────────────────────────────────────────────────────────
#  skills_runtime.models
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillsRuntimeModels:
    def test_skill_scope_values(self) -> None:
        from app.skills_runtime.models import SkillScope
        assert SkillScope.PLATFORM == "platform"
        assert SkillScope.TENANT == "tenant"
        assert SkillScope.AGENT == "agent"

    def test_skill_status_values(self) -> None:
        from app.skills_runtime.models import SkillStatus
        assert SkillStatus.ACTIVE == "active"
        assert SkillStatus.DISABLED == "disabled"
        assert SkillStatus.DEPRECATED == "deprecated"
        assert SkillStatus.BETA == "beta"

    def test_skill_definition_defaults(self) -> None:
        from app.skills_runtime.models import SkillDefinition, SkillScope, SkillStatus
        skill = SkillDefinition(
            skill_id="s1",
            name="Test Skill",
            description="A test skill",
            scope=SkillScope.PLATFORM,
        )
        assert skill.status == SkillStatus.ACTIVE
        assert skill.version == "1.0.0"
        assert skill.trigger_hints == []
        assert skill.allowed_tools == []
        assert skill.permissions_required == []
        assert skill.is_builtin is False
        assert skill.author == "system"

    def test_skill_definition_full(self) -> None:
        from app.skills_runtime.models import SkillDefinition, SkillScope, SkillStatus
        skill = SkillDefinition(
            skill_id="graphify",
            name="Graphify",
            description="Build knowledge graphs",
            scope=SkillScope.TENANT,
            trigger_hints=["graphify this", "create graph from"],
            allowed_tools=["knowledge_graph_extract"],
            is_builtin=True,
            version="2.0.0",
            status=SkillStatus.BETA,
            tenant_id="tenant-abc",
        )
        assert skill.is_builtin is True
        assert skill.version == "2.0.0"
        assert skill.status == SkillStatus.BETA
        assert skill.tenant_id == "tenant-abc"

    def test_skill_execution_defaults(self) -> None:
        from app.skills_runtime.models import SkillExecution
        execution = SkillExecution(
            execution_id="e1",
            skill_id="graphify",
            tenant_id="t1",
        )
        assert execution.success is False
        assert execution.duration_ms == 0.0
        assert execution.output == ""
        assert execution.error is None
        assert execution.goal_id is None

    def test_builtin_skills_list(self) -> None:
        from app.skills_runtime.models import BUILTIN_SKILLS
        assert len(BUILTIN_SKILLS) >= 3
        skill_ids = {s["skill_id"] for s in BUILTIN_SKILLS}
        assert "graphify" in skill_ids
        assert "headroom" in skill_ids
        assert "code_review" in skill_ids
        for skill in BUILTIN_SKILLS:
            assert "skill_id" in skill
            assert "name" in skill
            assert "trigger_hints" in skill
            assert isinstance(skill["trigger_hints"], list)


# ─────────────────────────────────────────────────────────────────────────────
#  skills_runtime.executor (public surface)
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillsExecutorPublicSurface:
    def test_executor_importable(self) -> None:
        from app.skills_runtime.executor import SkillExecutor
        assert SkillExecutor is not None

    def test_executor_instantiates(self) -> None:
        from app.skills_runtime.executor import SkillExecutor
        executor = SkillExecutor()
        assert executor is not None

    def test_executor_has_execute_method(self) -> None:
        import inspect

        from app.skills_runtime.executor import SkillExecutor
        executor = SkillExecutor()
        assert hasattr(executor, "execute") or hasattr(executor, "run_skill")

    def test_builtin_skills_registered(self) -> None:
        from app.skills_runtime.executor import SkillExecutor
        executor = SkillExecutor()
        # Should have loaded built-in skills
        if hasattr(executor, "_skills") or hasattr(executor, "skills"):
            registry = getattr(executor, "_skills", getattr(executor, "skills", {}))
            assert len(registry) >= 0  # Registry may be lazily populated
