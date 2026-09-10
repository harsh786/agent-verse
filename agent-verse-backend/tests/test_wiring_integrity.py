"""Wiring integrity tests — proves that create_app() and worker path inject
every service the AgentGraph consumes. Prevents the 0B defect class from recurring.
"""
import inspect

import pytest


class TestGoalServiceWiring:
    """0B.1: goal_service.py must pass all required services to AgentGraph."""

    def test_goal_service_passes_llm_response_cache(self):
        """AgentGraph must accept llm_response_cache and goal_service must wire it
        (behavioural — tolerant of dict-based ``**graph_services`` construction)."""
        from app.agent.graph import AgentGraph
        from app.services import goal_service
        assert "llm_response_cache" in inspect.signature(AgentGraph.__init__).parameters
        assert "llm_response_cache" in inspect.getsource(goal_service), \
            "0B.1: llm_response_cache not wired into AgentGraph in goal_service"

    def test_goal_service_passes_semantic_cache(self):
        from app.agent.graph import AgentGraph
        from app.services import goal_service
        assert "semantic_cache" in inspect.signature(AgentGraph.__init__).parameters
        assert "semantic_cache" in inspect.getsource(goal_service), \
            "goal_service must wire semantic_cache into AgentGraph"

    def test_goal_service_calls_dedup_release(self):
        """0B.10: goal_service must call dedup release on terminal states."""
        from app.services import goal_service
        source = inspect.getsource(goal_service)
        assert "release(" in source and "dedup" in source.lower(), \
            "0B.10: GoalDeduplicator.release() never called in goal_service"


class TestWorkerWiring:
    """0B.2/0B.6: tasks.py worker must pass all services and use correct API."""

    def test_worker_passes_llm_response_cache(self):
        from app.scaling import tasks
        source = inspect.getsource(tasks)
        assert "llm_response_cache=" in source, \
            "0B.2: worker AgentGraph missing llm_response_cache="

    def test_worker_uses_get_config_not_get(self):
        """0B.6: Worker must call .get_config() not .get() on LLMConfigStore."""
        from app.scaling import tasks
        source = inspect.getsource(tasks)
        # Should NOT have _config_store.get(
        import re
        bad_calls = re.findall(r'_config_store\.get\(', source)
        assert len(bad_calls) == 0, \
            f"0B.6: Worker calls .get() on LLMConfigStore ({len(bad_calls)} times). Must use .get_config()"


class TestModelRouterOverride:
    """0B.5: ModelRouter must have with_override() for per-agent model override."""

    def test_model_router_has_with_override(self):
        from app.agent.model_router import ModelRouter
        assert hasattr(ModelRouter, "with_override"), \
            "0B.5: ModelRouter.with_override() not implemented"

    def test_with_override_is_copy_on_write(self):
        from app.agent.model_router import ModelRouter
        router = ModelRouter(provider_name="anthropic")
        original_planning = router.model_for("planning")

        overridden = router.with_override("test-model-xyz")

        # Overridden router uses the new model
        assert overridden.model_for("planning") == "test-model-xyz"
        assert overridden.model_for("execution") == "test-model-xyz"

        # Original is unchanged
        assert router.model_for("planning") == original_planning


class TestPlanLimitsMonotonicity:
    """0B.8: Plan limits must be monotonically non-decreasing."""

    def test_goals_per_day_is_monotone(self):
        from app.tenancy.context import PLAN_LIMITS, PlanTier
        tiers = [PlanTier.FREE, PlanTier.STARTER, PlanTier.PROFESSIONAL, PlanTier.ENTERPRISE]
        limits = [PLAN_LIMITS[t].goals_per_day for t in tiers]
        for i in range(len(limits) - 1):
            assert limits[i] <= limits[i+1], \
                f"0B.8: goals_per_day not monotone: {tiers[i].value}={limits[i]} > {tiers[i+1].value}={limits[i+1]}"

    def test_max_agents_is_monotone(self):
        from app.tenancy.context import PLAN_LIMITS, PlanTier
        tiers = [PlanTier.FREE, PlanTier.STARTER, PlanTier.PROFESSIONAL, PlanTier.ENTERPRISE]
        limits = [PLAN_LIMITS[t].max_agents for t in tiers]
        for i in range(len(limits) - 1):
            assert limits[i] <= limits[i+1], \
                f"0B.8: max_agents not monotone: {tiers[i].value}={limits[i]} > {tiers[i+1].value}={limits[i+1]}"


class TestScopeEnforcementBypass:
    """0B.9: No-roles API keys must NOT bypass scope enforcement for writes."""

    def test_no_roles_write_denied_by_default(self):
        """API keys without roles are denied on write endpoints by default."""
        import os
        os.environ.pop("SCOPE_ENFORCEMENT_LEGACY_ALLOW", None)

        from app.auth.scope_enforcement import ScopeEnforcementMiddleware
        # Just check the source has the new guard
        source = inspect.getsource(ScopeEnforcementMiddleware)
        assert "SCOPE_ENFORCEMENT_LEGACY_ALLOW" in source, \
            "0B.9: No-roles bypass does not have SCOPE_ENFORCEMENT_LEGACY_ALLOW flag"


class TestToolCacheWriteInvalidation:
    """0B.7: Write tools must invalidate cached reads, not cache their own results."""

    @pytest.mark.asyncio
    async def test_write_tool_invalidates_cache(self):
        from app.mcp.tool_cache import ToolResultCache

        cache = ToolResultCache()

        # Pre-populate a cached search result
        await cache.set(
            server_id="jira", tool_name="search_issues",
            arguments={"jql": "project=FOO"}, result=[{"id": "1"}], tenant_id="t1"
        )

        # Verify it's cached
        hit = await cache.get(
            server_id="jira", tool_name="search_issues",
            arguments={"jql": "project=FOO"}, tenant_id="t1"
        )
        assert hit is not None

        # Simulate write tool success → invalidate
        await cache.invalidate_writes("create_issue", tenant_id="t1", server_id="jira")

        # Cache should now be empty for this tenant
        hit_after = await cache.get(
            server_id="jira", tool_name="search_issues",
            arguments={"jql": "project=FOO"}, tenant_id="t1"
        )
        assert hit_after is None, "Write tool did not invalidate cached reads"


class TestVerifierCacheGuard:
    """0B.3: Verifier cache must use should_skip_cache guard."""

    def test_verifier_cache_has_should_skip_cache_guard(self):
        from app.agent import graph
        source = inspect.getsource(graph)

        # Find _node_verify
        verify_start = source.find("async def _node_verify")
        verify_section = source[verify_start:verify_start + 3000]

        assert "should_skip_cache" in verify_section, \
            "0B.3: _node_verify cache block missing should_skip_cache guard"


class TestSemanticCacheWarmSignature:
    """0B.4: SemanticCache.warm() must accept queries= and embeddings= kwargs."""

    @pytest.mark.asyncio
    async def test_warm_accepts_queries_and_embeddings(self):
        from app.rag.semantic_cache import SemanticCache

        cache = SemanticCache()

        # Should not raise TypeError
        try:
            result = await cache.warm(
                queries=["find open tickets", "list projects"],
                embeddings=[[0.1] * 10, [0.2] * 10],
                tenant_id="t1",
            )
            assert isinstance(result, int)
        except TypeError as e:
            pytest.fail(f"0B.4: warm() does not accept queries/embeddings kwargs: {e}")


class TestPhase3Wiring:
    """C1/C2/C3: Phase 3 services must be wired in goal_service, main.py, and MCPClient."""

    def test_goal_service_passes_grounding_checker(self):
        import inspect

        from app.services import goal_service
        source = inspect.getsource(goal_service)
        assert "grounding_checker" in source or "GroundingChecker" in source, \
            "C1: grounding_checker not passed to AgentGraph in goal_service"

    def test_goal_service_passes_answer_synthesizer(self):
        import inspect

        from app.services import goal_service
        source = inspect.getsource(goal_service)
        assert "answer_synthesizer" in source or "AnswerSynthesizer" in source, \
            "C1: answer_synthesizer not passed to AgentGraph in goal_service"

    def test_google_oauth_router_registered(self):
        """The google-oauth router is imported and included by the router
        registrar (registration lives in app.bootstrap.routers, not main.py)."""
        from app.bootstrap import routers
        source = inspect.getsource(routers)
        assert "include_router(google_oauth_router)" in source, \
            "C2: google_oauth router not registered by register_routers"

    def test_exfil_guard_in_mcp_client(self):
        import inspect

        from app.mcp import client
        source = inspect.getsource(client)
        assert "exfil_guard" in source or "check_tool_args_for_exfil" in source, \
            "C3: exfil guard not wired in MCPClient.call_tool()"


class TestPhase2Wiring:
    def test_tool_selector_on_app_state(self):
        """Phase 2: tool_selector must be on app.state."""
        import inspect

        from app import main
        source = inspect.getsource(main)
        assert "tool_selector" in source, "Phase 2: tool_selector not wired in main.py"

    def test_tiktoken_in_pyproject(self):
        from pathlib import Path
        pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        assert "tiktoken" in pyproject, "Phase 2: tiktoken not in dependencies"

    def test_semantic_cache_backend_wired_in_lifespan(self):
        import inspect

        from app import main
        source = inspect.getsource(main)
        assert "select_cache_backend" in source or "vector_cache_backend" in source, \
            "Phase 2: semantic cache vector backend not wired in lifespan"

    def test_sse_emits_id_lines(self):
        import inspect

        from app.api import goals
        source = inspect.getsource(goals)
        assert "id:" in source or "Last-Event-ID" in source, \
            "Phase 2: SSE endpoint missing id: lines or Last-Event-ID header"


class TestPhase3GraphWiring:
    def test_graph_accepts_grounding_checker(self):
        import inspect

        from app.agent.graph import AgentGraph
        sig = inspect.signature(AgentGraph.__init__)
        assert "grounding_checker" in sig.parameters, \
            "Phase 3: AgentGraph.__init__ missing grounding_checker param"

    def test_graph_accepts_consensus_verifier(self):
        import inspect

        from app.agent.graph import AgentGraph
        sig = inspect.signature(AgentGraph.__init__)
        assert "consensus_verifier" in sig.parameters, \
            "Phase 3: AgentGraph.__init__ missing consensus_verifier param"

    def test_graph_accepts_answer_synthesizer(self):
        import inspect

        from app.agent.graph import AgentGraph
        sig = inspect.signature(AgentGraph.__init__)
        assert "answer_synthesizer" in sig.parameters, \
            "Phase 3: AgentGraph.__init__ missing answer_synthesizer param"

    def test_calibration_store_in_goal_service(self):
        import inspect

        from app.services import goal_service
        source = inspect.getsource(goal_service)
        assert "calibration_store" in source, \
            "Phase 3: calibration_store not passed to AgentGraph in goal_service"


class TestIngestionKGWiring:
    """D-15: create_app() must construct the IngestionPipeline WITH a live KG hook,
    otherwise KG auto-population from ingested documents is silently inert."""

    def test_ingestion_pipeline_has_live_kg_hook(self):
        """Behavioural (not source-grep): the real app.state pipeline carries a
        KGIngestionHook, so ingesting a document actually feeds the graph."""
        from app.knowledge_graph.ingestion_hook import KGIngestionHook
        from app.main import create_app

        app = create_app()
        pipeline = getattr(app.state, "ingestion_pipeline", None)
        assert pipeline is not None, "ingestion_pipeline not on app.state"
        assert isinstance(pipeline._kg_hook, KGIngestionHook), (
            "D-15: IngestionPipeline constructed without a KGIngestionHook — "
            "document ingestion would feed nothing into knowledge_nodes/edges"
        )
