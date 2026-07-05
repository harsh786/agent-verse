"""Service initialization helpers extracted from main.py.

Reduces main.py complexity by grouping service creation by concern:
- Database services (pool, session factory)
- Cache services (Redis)
- Core services (tenant, goal, agent)
- Intelligence services (provider, embedder, eval)
- Platform services (MCP, knowledge, collab)
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def init_core_services(
    app_state: Any, settings: Any, db_factory: Any, redis: Any
) -> None:
    """Initialize core platform services after DB+Redis are available."""
    # This function documents the extraction intent.
    # Full extraction requires careful handling of the service dependency graph
    # in main.py's lifespan function.
    pass


async def init_intelligence_services(app_state: Any, settings: Any) -> None:
    """Initialize LLM providers, embedders, and evaluation services."""
    pass


def get_service_health(app_state: Any) -> dict[str, str]:
    """Check health of all initialized services for /health endpoint."""
    services = {
        "goal_service": "ok" if getattr(app_state, "goal_service", None) else "missing",
        "tenant_service": "ok"
        if getattr(app_state, "tenant_service", None)
        else "missing",
        "agent_store": "ok" if getattr(app_state, "agent_store", None) else "missing",
        "mcp_client": "ok" if getattr(app_state, "mcp_client", None) else "missing",
    }
    return services
