"""Tests for workflow executor node dispatch with new node types."""
import pytest


class TestWorkflowExecutorDispatch:
    @pytest.mark.asyncio
    async def test_executor_threads_rag_config_and_gateway(self):
        from app.agent.workflow_executor import WorkflowExecutor
        from app.agent.workflow_planner import WorkflowPlan
        from app.rag.contracts import RAGExecutionResult, RAGStrategy
        from app.tenancy.context import PlanTier, TenantContext

        calls = []

        class Gateway:
            async def execute(self, tenant_ctx, **kwargs):
                calls.append((tenant_ctx, kwargs))
                return RAGExecutionResult(
                    requested_strategy_id="fusion",
                    resolved_strategy_id=RAGStrategy.FUSION,
                )

        plan = WorkflowPlan.from_dict(
            {
                "steps": [
                    {
                        "id": "rag-1",
                        "description": "policy",
                        "tool": "rag",
                        "collection_id": "collection-1",
                        "strategy": "fusion",
                        "top_k": 7,
                        "filters": {"team": "legal"},
                    }
                ]
            },
            goal="policy",
        )
        tenant = TenantContext("t1", PlanTier.PROFESSIONAL, "k1")

        result = await WorkflowExecutor(retrieval_gateway=Gateway()).execute(
            plan,
            tenant_ctx=tenant,
        )

        assert result["status"] == "complete"
        assert calls[0][0] is tenant
        assert calls[0][1]["collection_id"] == "collection-1"
        assert calls[0][1]["strategy_id"] is RAGStrategy.FUSION
        assert calls[0][1]["top_k"] == 7
        assert calls[0][1]["filters"] == {"team": "legal"}

    def test_workflow_executor_importable(self):
        from app.agent.workflow_executor import WorkflowExecutor
        assert WorkflowExecutor is not None

    def test_workflow_nodes_importable(self):
        from app.agent.workflow_nodes import (
            execute_decision_node,
            execute_loop_node,
            execute_delay_node,
            execute_rag_node,
            execute_skill_node,
        )
        assert all([
            execute_decision_node,
            execute_loop_node,
            execute_delay_node,
            execute_rag_node,
            execute_skill_node,
        ])

    def test_executor_has_llm_provider_attr(self):
        from app.agent.workflow_executor import WorkflowExecutor
        ex = WorkflowExecutor()
        assert hasattr(ex, "_llm_provider")

    def test_executor_has_embedder_attr(self):
        from app.agent.workflow_executor import WorkflowExecutor
        ex = WorkflowExecutor()
        assert hasattr(ex, "_embedder")

    def test_executor_imports_node_functions(self):
        """Verify the 5 node functions are imported at module level."""
        import inspect
        from app.agent import workflow_executor
        source = inspect.getsource(workflow_executor)
        for fn in [
            "execute_decision_node",
            "execute_loop_node",
            "execute_delay_node",
            "execute_rag_node",
            "execute_skill_node",
        ]:
            assert fn in source, f"workflow_executor.py missing import of {fn}"

    @pytest.mark.asyncio
    async def test_decision_node_executes(self):
        from app.agent.workflow_nodes import execute_decision_node
        result = await execute_decision_node(
            {"condition": "True", "true_edge": "yes", "false_edge": "no"},
            {},
        )
        assert result == "yes"

    @pytest.mark.asyncio
    async def test_decision_node_false_branch(self):
        from app.agent.workflow_nodes import execute_decision_node
        result = await execute_decision_node(
            {"condition": "False", "true_edge": "yes", "false_edge": "no"},
            {},
        )
        assert result == "no"

    @pytest.mark.asyncio
    async def test_loop_node_executes(self):
        from app.agent.workflow_nodes import execute_loop_node
        result = await execute_loop_node(
            {"items_key": "items", "max_iter": 3},
            {"items": [1, 2, 3]},
        )
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_delay_node_executes(self):
        from app.agent.workflow_nodes import execute_delay_node
        result = await execute_delay_node(
            {"seconds": 0, "reason": "test"},
            {},
        )
        assert "delayed_seconds" in result
        assert result["delayed_seconds"] == 0.0

    @pytest.mark.asyncio
    async def test_rag_node_requires_collection(self):
        from app.agent.workflow_nodes import execute_rag_node
        from app.tenancy.context import PlanTier, TenantContext

        with pytest.raises(ValueError, match="collection_id"):
            await execute_rag_node(
                {"collection_id": "", "query_template": "test query", "top_k": 5},
                {},
                retrieval_gateway=object(),
                tenant_ctx=TenantContext("t1", PlanTier.PROFESSIONAL, "k1"),
            )

    @pytest.mark.asyncio
    async def test_skill_node_executes(self):
        from app.agent.workflow_nodes import execute_skill_node
        result = await execute_skill_node({"goal": "review code"}, {})
        assert "skill_instructions" in result

    @pytest.mark.asyncio
    async def test_executor_dispatches_decision_node(self):
        """WorkflowExecutor._execute_step routes 'decision' tool to execute_decision_node."""
        from app.agent.workflow_executor import WorkflowExecutor
        from app.agent.workflow_planner import WorkflowStep

        ex = WorkflowExecutor()
        step = WorkflowStep(
            id="s1",
            description="True",
            tool="decision",
        )
        result = await ex._execute_step(step, tenant_ctx=None, prior_results={})
        assert result["status"] == "complete"
        assert result.get("node_type") == "decision"

    @pytest.mark.asyncio
    async def test_executor_dispatches_skill_node(self):
        """WorkflowExecutor._execute_step routes 'skill' tool to execute_skill_node."""
        from app.agent.workflow_executor import WorkflowExecutor
        from app.agent.workflow_planner import WorkflowStep

        ex = WorkflowExecutor()
        step = WorkflowStep(
            id="s2",
            description="review code for security",
            tool="skill",
        )
        result = await ex._execute_step(step, tenant_ctx=None, prior_results={})
        assert result["status"] == "complete"
        assert result.get("node_type") == "skill"
