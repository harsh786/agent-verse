"""Builders for the durable connector registry, used by the API and every worker.

Each process that dispatches connector tools (API lifespan, Celery goal worker,
mission publisher, workflow worker) must read connectors from the same durable
store; these builders keep that one decision in one place.
"""

from __future__ import annotations

from typing import Any

from app.mcp.registry import MCPRegistry


def _default_db_factory() -> Any:
    from app.db.session import get_session_factory

    return get_session_factory()


def build_connector_registry(
    redis: Any, *, db_factory: Any = None, auto_provision_builtins: bool = True
) -> MCPRegistry:
    """A Postgres-backed registry with ``redis`` as its read cache."""
    return MCPRegistry(
        redis,
        auto_provision_builtins=auto_provision_builtins,
        db_factory=db_factory if db_factory is not None else _default_db_factory(),
    )


def build_connector_secret_store(redis: Any, *, db_factory: Any = None) -> Any:
    """The Postgres-backed connector secret store with ``redis`` as its cache."""
    from app.mcp.connector_secrets import DurableConnectorSecretStore

    return DurableConnectorSecretStore(
        db_factory=db_factory if db_factory is not None else _default_db_factory(),
        redis=redis,
    )


__all__ = ["build_connector_registry", "build_connector_secret_store"]
