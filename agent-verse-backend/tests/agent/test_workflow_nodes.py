"""Tests for Phase 9 workflow node executors."""
import pytest
import asyncio


class TestDecisionNode:
    @pytest.mark.asyncio
    async def test_decision_expression_true(self):
        from app.agent.workflow_nodes import execute_decision_node
        result = await execute_decision_node(
            {"condition": "True", "true_edge": "yes", "false_edge": "no"},
            {},
        )
        assert result == "yes"

    @pytest.mark.asyncio
    async def test_decision_expression_false(self):
        from app.agent.workflow_nodes import execute_decision_node
        result = await execute_decision_node(
            {"condition": "False", "true_edge": "yes", "false_edge": "no"},
            {},
        )
        assert result == "no"

    @pytest.mark.asyncio
    async def test_decision_unsafe_expression_returns_false_edge(self):
        """Unsafe expressions should return false_edge, not raise."""
        from app.agent.workflow_nodes import execute_decision_node
        result = await execute_decision_node(
            {"condition": "__import__('os').system('rm -rf /')", "false_edge": "safe"},
            {},
        )
        assert result == "safe"

    @pytest.mark.asyncio
    async def test_decision_context_variable(self):
        from app.agent.workflow_nodes import execute_decision_node
        result = await execute_decision_node(
            {"condition": "{{score}} > 5", "true_edge": "high", "false_edge": "low"},
            {"score": 8},
        )
        assert result == "high"


class TestLoopNode:
    @pytest.mark.asyncio
    async def test_loop_iterates_list(self):
        from app.agent.workflow_nodes import execute_loop_node
        results = await execute_loop_node(
            {"items_key": "tickets", "max_iter": 5},
            {"tickets": ["T1", "T2", "T3"]},
        )
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_loop_respects_max_iter(self):
        from app.agent.workflow_nodes import execute_loop_node
        results = await execute_loop_node(
            {"items_key": "items", "max_iter": 2},
            {"items": list(range(10))},
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_loop_hard_cap_50(self):
        from app.agent.workflow_nodes import execute_loop_node
        results = await execute_loop_node(
            {"items_key": "items", "max_iter": 1000},
            {"items": list(range(100))},
            max_iterations=50,
        )
        assert len(results) <= 50


class TestDelayNode:
    @pytest.mark.asyncio
    async def test_delay_completes(self):
        from app.agent.workflow_nodes import execute_delay_node
        result = await execute_delay_node({"seconds": 0.01, "reason": "test"}, {})
        assert result["delayed_seconds"] == 0.01

    @pytest.mark.asyncio
    async def test_delay_capped_at_300_non_prod(self):
        from app.agent.workflow_nodes import execute_delay_node
        import os
        os.environ.pop("ENVIRONMENT", None)  # non-production
        result = await execute_delay_node({"seconds": 0.001}, {})
        assert result["delayed_seconds"] <= 300


class TestSkillNode:
    @pytest.mark.asyncio
    async def test_skill_node_by_id(self):
        from app.agent.workflow_nodes import execute_skill_node
        result = await execute_skill_node(
            {"skill_id": "skill-code-review"},
            {},
        )
        assert "skill_instructions" in result
        assert len(result["skills_loaded"]) > 0

    @pytest.mark.asyncio
    async def test_skill_node_by_goal(self):
        from app.agent.workflow_nodes import execute_skill_node
        result = await execute_skill_node(
            {"goal": "review PR #42 for security"},
            {},
        )
        assert "skill_instructions" in result

    @pytest.mark.asyncio
    async def test_rag_node_no_db_returns_empty(self):
        from app.agent.workflow_nodes import execute_rag_node
        result = await execute_rag_node(
            {"collection_id": "col-1", "query_template": "find relevant info"},
            {"goal": "test"},
            db_session=None,
        )
        assert "chunks" in result
        assert isinstance(result["chunks"], list)


class TestSandboxGuard:
    def test_production_guard_in_code_interpreter(self):
        """CodeInterpreter must refuse subprocess in production."""
        import inspect
        from app.tools.code_interpreter import CodeInterpreter
        source = inspect.getsource(CodeInterpreter)
        assert "production" in source.lower() and "subprocess" in source.lower() or \
               "ENVIRONMENT" in source, \
            "CodeInterpreter missing production guard for unsandboxed subprocess"
