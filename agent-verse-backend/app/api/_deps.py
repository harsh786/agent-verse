"""Typed FastAPI dependency functions for app.state services.

Using ``Depends(get_X)`` instead of ``request.app.state.X`` directly:
  - Makes dependencies explicit and testable via ``dependency_overrides``
  - Enables IDE type inference on route parameters
  - Allows mocking in unit tests without constructing a full FastAPI app

Usage in route handlers::

    @router.get("/goals")
    async def list_goals(
        svc: GoalService = Depends(get_goal_service),
        tenant: TenantContext = Depends(get_tenant_context),
    ):
        ...

Test override::

    app.dependency_overrides[get_goal_service] = lambda: FakeGoalService()
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request


# ---------------------------------------------------------------------------
# Core services
# ---------------------------------------------------------------------------


def get_goal_service(request: Request) -> Any:
    """Return the per-app GoalService from app.state."""
    return request.app.state.goal_service


def get_tenant_service(request: Request) -> Any:
    """Return the per-app TenantService from app.state."""
    return request.app.state.tenant_service


def get_mcp_registry(request: Request) -> Any:
    """Return the per-app MCPRegistry from app.state."""
    return request.app.state.mcp_registry


def get_mcp_client(request: Request) -> Any:
    """Return the per-app MCPClient from app.state."""
    return request.app.state.mcp_client


def get_agent_store(request: Request) -> Any:
    """Return the per-app AgentStore from app.state."""
    return request.app.state.agent_store


def get_meta_agent(request: Request) -> Any:
    """Return the per-app MetaAgentPlanner from app.state."""
    return request.app.state.meta_agent


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


def get_hitl_gateway(request: Request) -> Any:
    """Return the per-app HITLGateway from app.state."""
    return request.app.state.hitl_gateway


def get_audit_log(request: Request) -> Any:
    """Return the per-app AuditLog from app.state."""
    return request.app.state.audit_log


def get_cost_controller(request: Request) -> Any:
    """Return the per-app CostController from app.state."""
    return request.app.state.cost_controller


def get_policy_engine(request: Request) -> Any:
    """Return the per-app PolicyEngine from app.state."""
    return request.app.state.policy_engine


# ---------------------------------------------------------------------------
# Knowledge / memory
# ---------------------------------------------------------------------------


def get_knowledge_store(request: Request) -> Any:
    """Return the per-app KnowledgeStore from app.state."""
    return request.app.state.knowledge_store


def get_semantic_cache(request: Request) -> Any:
    """Return the per-app SemanticCache from app.state."""
    return request.app.state.semantic_cache


def get_long_term_memory(request: Request) -> Any:
    """Return the per-app LongTermMemoryStore from app.state."""
    return request.app.state.long_term_memory


# ---------------------------------------------------------------------------
# Intelligence / eval
# ---------------------------------------------------------------------------


def get_eval_runner(request: Request) -> Any:
    """Return the per-app EvalRunner from app.state."""
    return request.app.state.eval_runner


def get_simulation_runner(request: Request) -> Any:
    """Return the per-app SimulationRunner from app.state."""
    return request.app.state.simulation_runner


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def get_schedule_store(request: Request) -> Any:
    """Return the per-app ScheduleStore from app.state."""
    return request.app.state.schedule_store


def get_nl_scheduler(request: Request) -> Any:
    """Return the per-app NLScheduler from app.state."""
    return request.app.state.nl_scheduler


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_settings(request: Request) -> Any:
    """Return the per-app Settings from app.state."""
    return request.app.state.settings
