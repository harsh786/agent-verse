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

# ---------------------------------------------------------------------------
# Extended services (added to complete full DI coverage)
# ---------------------------------------------------------------------------


def get_compliance_controller(request: Request) -> Any:
    return request.app.state.compliance_controller


def get_red_team_runner(request: Request) -> Any:
    return request.app.state.red_team_runner


def get_marketplace(request: Request) -> Any:
    return request.app.state.marketplace


def get_self_optimizer(request: Request) -> Any:
    return request.app.state.self_optimizer


def get_health_registry(request: Request) -> Any:
    return request.app.state.health


def get_mcp_client_dep(request: Request) -> Any:
    """Alias for get_mcp_client — used where naming conflicts exist."""
    return request.app.state.mcp_client


def get_strategy_registry(request: Request) -> Any:
    return request.app.state.strategy_registry


def get_strategy_certification(request: Request) -> Any:
    return request.app.state.strategy_certification


def get_strategy_readiness(request: Request) -> Any:
    return request.app.state.strategy_readiness


def get_auction_repository(request: Request) -> Any:
    return request.app.state.auction_repository


def get_auction_bid_inbox(request: Request) -> Any:
    return request.app.state.auction_bid_inbox


def get_swarm_repository(request: Request) -> Any:
    return request.app.state.swarm_repository


def get_camel_repository(request: Request) -> Any:
    return request.app.state.camel_repository


def get_generative_repository(request: Request) -> Any:
    return request.app.state.generative_repository


def get_collab_store(request: Request) -> Any:
    return request.app.state.collab_store


def get_connector_secret_store(request: Request) -> Any:
    return getattr(request.app.state, "connector_secret_store", None)


def get_rpa_session_store(request: Request) -> Any:
    return getattr(request.app.state, "rpa_session_store", None)


def get_rpa_executor(request: Request) -> Any:
    return getattr(request.app.state, "rpa_executor", None)


def get_llm_configs(request: Request) -> Any:
    return getattr(request.app.state, "_llm_configs", {})


def get_policy_registry(request: Request) -> Any:
    return getattr(request.app.state, "_policy_registry", None)


def get_webhook_tokens(request: Request) -> Any:
    return getattr(request.app.state, "_webhook_tokens", {})


def get_budget_config(request: Request) -> Any:
    return getattr(request.app.state, "_budget_config", None)


def get_cache_stats(request: Request) -> Any:
    return getattr(request.app.state, "_cache_stats", None)
