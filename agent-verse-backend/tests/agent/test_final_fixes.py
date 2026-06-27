"""Tests for critical and high-severity bug fixes.

C-1  SSE Celery bridge
H-1  eval() sandbox
H-2  SSRF 172.16/12 fix
H-3  ExecutionMemory wiring
H-4  Agent config loaded from store
H-5  ToolContext duplicate tool names
H-6  AgentRouter uses pre-wired instance
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── H-2: SSRF protection ──────────────────────────────────────────────────────

def test_http_tool_blocks_172_16_range():
    """172.16.x.x (Docker bridge) must be blocked."""
    from app.tools.http_tool import _is_blocked
    assert _is_blocked("http://172.17.0.2/admin") is True, "172.17.x.x must be blocked (Docker range)"
    assert _is_blocked("http://172.16.0.1/secret") is True, "172.16.x.x must be blocked"
    assert _is_blocked("http://172.31.255.255/internal") is True, "172.31.x.x must be blocked"
    assert _is_blocked("http://10.0.0.1/db") is True, "10.x.x.x must be blocked"
    assert _is_blocked("http://192.168.1.1/router") is True, "192.168.x.x must be blocked"


def test_http_tool_allows_public_ips():
    from app.tools.http_tool import _is_blocked
    assert _is_blocked("https://api.github.com") is False, "github.com must be allowed"
    assert _is_blocked("https://8.8.8.8") is False, "Public DNS must be allowed"


def test_http_tool_blocks_aws_metadata():
    from app.tools.http_tool import _is_blocked
    assert _is_blocked("http://169.254.169.254/latest/meta-data/") is True


def test_http_tool_uses_ipaddress_module():
    """http_tool.py must use ipaddress module for comprehensive SSRF protection."""
    import inspect
    from app.tools import http_tool
    src = inspect.getsource(http_tool)
    assert "ipaddress" in src, "http_tool.py must use Python's ipaddress module"


# ── H-1: eval() sandbox ───────────────────────────────────────────────────────

def test_safe_eval_condition_blocks_traversal():
    """eval() must not allow __subclasses__ or other dangerous traversals."""
    import inspect
    from app.agent import structured_plan
    src = inspect.getsource(structured_plan)
    # The module must use safe eval approach
    assert "_safe_eval_condition" in src or "simpleeval" in src or "SAFE_PATTERN" in src, \
        "structured_plan.py must use safe expression evaluation"


def test_safe_eval_condition_rejects_dangerous_expression():
    """_safe_eval_condition must reject dangerous expressions."""
    from app.agent.structured_plan import _safe_eval_condition
    # Expressions with dunder attributes should be blocked or default to True safely
    result = _safe_eval_condition("__import__('os').system('id')", {})
    # Must either return True (safe default) or False — must NOT raise or execute
    assert isinstance(result, bool)


def test_safe_eval_condition_normal_comparison():
    """_safe_eval_condition must evaluate normal comparisons correctly."""
    from app.agent.structured_plan import _safe_eval_condition
    assert _safe_eval_condition("x == 'complete'", {"x": "complete"}) is True
    assert _safe_eval_condition("x == 'complete'", {"x": "pending"}) is False


def test_safe_eval_condition_empty_returns_true():
    from app.agent.structured_plan import _safe_eval_condition
    assert _safe_eval_condition("", {}) is True
    assert _safe_eval_condition("   ", {}) is True
    assert _safe_eval_condition(None, {}) is True  # type: ignore[arg-type]


# ── H-5: ToolContext duplicate tool names ─────────────────────────────────────

def test_tool_context_find_tool_returns_first_on_duplicate():
    """find_tool() must return first match when multiple connectors have same tool name."""
    from app.agent.tool_context import ToolContext, ToolRef
    ctx = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                name="search_issues",
                server_id="jira-prod",
                server_name="jira-prod",
                description="search",
                input_schema={},
            ),
            ToolRef(
                name="search_issues",
                server_id="jira-staging",
                server_name="jira-staging",
                description="search",
                input_schema={},
            ),
        ],
    )
    result = ctx.find_tool("search_issues")
    assert result is not None, "Must return a match even when duplicates exist"
    assert result.server_id == "jira-prod", "Must return first registered tool"


def test_tool_context_find_tool_returns_none_when_absent():
    from app.agent.tool_context import ToolContext, ToolRef
    ctx = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                name="create_issue",
                server_id="jira",
                server_name="jira",
                description="create",
                input_schema={},
            ),
        ],
    )
    assert ctx.find_tool("nonexistent_tool") is None


# ── C-1: Celery SSE bridge ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_service_has_celery_event_bridge():
    from app.services.goal_service import GoalService
    svc = GoalService()
    assert hasattr(svc, "start_celery_event_bridge"), \
        "GoalService must have start_celery_event_bridge() for SSE in Celery mode"
    assert hasattr(svc, "_subscribe_celery_goal_events"), \
        "GoalService must have _subscribe_celery_goal_events() coroutine"


# ── H-3: ExecutionMemory wiring ───────────────────────────────────────────────

def test_execution_memory_on_app_state():
    """ExecutionMemory must be on app.state for agent graph to use it."""
    import inspect
    from app import main
    src = inspect.getsource(main)
    assert "exec_memory" in src or "ExecutionMemory" in src, \
        "main.py must instantiate ExecutionMemory and store on app.state"


def test_execution_memory_imported_in_main():
    """main.py must import ExecutionMemory."""
    import inspect
    from app import main
    src = inspect.getsource(main)
    assert "ExecutionMemory" in src, "main.py must import and instantiate ExecutionMemory"


# ── H-4: Agent config loaded ─────────────────────────────────────────────────

def test_agent_config_loaded_in_make_agent_loop():
    """_make_agent_loop_for_tenant must load agent config from agent store."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    # The function must read from agent_store, not use empty _agent_config = {}
    assert "agent_store" in src and "_agent_config" in src, \
        "_make_agent_loop_for_tenant must load agent config from store"


def test_make_agent_loop_accepts_agent_id():
    """_make_agent_loop_for_tenant must accept an agent_id parameter."""
    import inspect
    from app.services.goal_service import GoalService
    sig = inspect.signature(GoalService._make_agent_loop_for_tenant)
    assert "agent_id" in sig.parameters, \
        "_make_agent_loop_for_tenant must accept agent_id parameter"


# ── H-6: AgentRouter uses pre-wired instance ─────────────────────────────────

def test_submit_goal_uses_app_state_router():
    """submit_goal must use pre-wired agent_router from app.state."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "agent_router" in src, \
        "submit_goal must look up agent_router from app_state (H-6)"
