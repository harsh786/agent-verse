"""D-2: the real supervisor/debate reasoning nodes must be reachable from an
agent's configuration on the DEFAULT (non-dynamic-orchestration) path.

`AgentGraph` already adds the `supervisor`/`debate` nodes when
`enable_supervisor`/`enable_debate` are set (tested in test_agent_graph_build_gaps.py),
and the v2 GraphFactory path derives them from the runtime profile. But
`GoalService._make_agent_loop_for_tenant` extracts enable_self_refine /
self_consistency / tree_of_thoughts / peer_review from the agent config and
forwards them into the graph — while silently dropping enable_supervisor /
enable_debate. So an agent explicitly configured for supervisor/debate never got
those nodes unless dynamic orchestration happened to select them. These tests
lock the default-path threading.
"""

from __future__ import annotations

from typing import Any

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext


class _FakeAgentStore:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def get(self, agent_id: str, *, tenant_ctx: Any = None) -> dict[str, Any]:
        return dict(self._config)


def _svc_with_agent_config(config: dict[str, Any]) -> GoalService:
    svc = GoalService.__new__(GoalService)
    svc._audit_log = None
    svc._db = None
    svc._hitl = None
    svc._get_agent_store = lambda: _FakeAgentStore(config)  # type: ignore[method-assign]
    svc._get_mcp_client = lambda: None  # type: ignore[method-assign]
    svc._select_models_for_tenant = lambda tenant_ctx: {}  # type: ignore[method-assign]
    return svc


_CTX = TenantContext(tenant_id="t-superv", plan=PlanTier.FREE, api_key_id="k1")


def _node_names(graph: Any) -> set[str]:
    return set(graph._graph.get_graph().nodes.keys())


def test_default_path_enables_supervisor_from_agent_config() -> None:
    svc = _svc_with_agent_config({"enable_supervisor": True})
    graph = svc._make_agent_loop_for_tenant(_CTX, None, agent_id="a1")
    assert graph._enable_supervisor is True
    assert "supervisor" in _node_names(graph)


def test_default_path_enables_debate_from_agent_config() -> None:
    svc = _svc_with_agent_config({"enable_debate": True})
    graph = svc._make_agent_loop_for_tenant(_CTX, None, agent_id="a1")
    assert graph._enable_debate is True
    assert "debate" in _node_names(graph)


def test_default_path_no_supervisor_debate_by_default() -> None:
    """Regression guard: without the flags, the nodes stay absent (opt-in only)."""
    svc = _svc_with_agent_config({})
    graph = svc._make_agent_loop_for_tenant(_CTX, None, agent_id="a1")
    assert graph._enable_supervisor is False
    assert graph._enable_debate is False
    names = _node_names(graph)
    assert "supervisor" not in names
    assert "debate" not in names
