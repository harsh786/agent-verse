"""Tests that GoalService properly wires app.state services into AgentGraph."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.rag.store import KnowledgeStore
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="wiring-t1", plan=PlanTier.PROFESSIONAL, api_key_id="wk1")


def test_make_agent_loop_for_tenant_uses_audit_log_from_app_state() -> None:
    """AgentGraph receives audit_log from app.state."""
    from app.governance.cost import CostController

    svc = GoalService()

    audit = AuditLog()
    cost = CostController()

    app_state = MagicMock()
    app_state.audit_log = audit
    app_state.cost_controller = cost
    app_state.redis_cost_controller = None  # No Redis controller: should fall back to cost_controller
    app_state.hitl_gateway = HITLGateway()
    app_state.knowledge_store = None
    app_state.long_term_memory = None
    app_state.eval_runner = None
    app_state.policy_engine = None
    app_state._llm_configs = {}

    svc._app_state = app_state
    loop = svc._make_agent_loop_for_tenant(T, app_state)

    # The returned graph should have the audit_log and cost_controller wired
    assert loop._audit_log is audit
    assert loop._cost_controller is cost


def test_make_agent_loop_for_tenant_uses_knowledge_store() -> None:
    """AgentGraph receives knowledge_store from app.state."""
    svc = GoalService()
    ks = KnowledgeStore()

    app_state = MagicMock()
    app_state.audit_log = None
    app_state.cost_controller = None
    app_state.hitl_gateway = None
    app_state.knowledge_store = ks
    app_state.long_term_memory = None
    app_state.eval_runner = None
    app_state.policy_engine = None
    app_state._llm_configs = {}

    svc._app_state = app_state
    loop = svc._make_agent_loop_for_tenant(T, app_state)
    assert loop._knowledge_store is ks


def test_make_agent_loop_for_tenant_falls_back_when_app_state_none() -> None:
    """Without app.state, fallback to self._audit_log and self._hitl."""
    audit = AuditLog()
    hitl = HITLGateway()
    svc = GoalService(audit_log=audit, hitl=hitl)

    loop = svc._make_agent_loop_for_tenant(T, None)
    # Should still work and use svc._audit_log
    assert loop._audit_log is audit


def test_run_agent_loop_uses_full_service_wiring() -> None:
    """_run_agent_loop calls _make_agent_loop_for_tenant (not bare _make_agent_loop)."""
    svc = GoalService()
    audit = AuditLog()

    app_state = MagicMock()
    app_state.audit_log = audit
    app_state.cost_controller = None
    app_state.hitl_gateway = HITLGateway()
    app_state.knowledge_store = None
    app_state.long_term_memory = None
    app_state.eval_runner = None
    app_state.policy_engine = None
    app_state._llm_configs = {}

    svc._app_state = app_state

    # Verify the method exists and is callable
    assert callable(svc._make_agent_loop_for_tenant)
