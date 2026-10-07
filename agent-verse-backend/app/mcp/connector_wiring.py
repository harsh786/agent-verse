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


def build_worker_mcp_client(
    redis: Any,
    *,
    db_factory: Any = None,
    llm_provider: Any = None,
    register_builtin_handlers: bool = True,
) -> Any:
    """The MCP client a Celery process dispatches connector tools with.

    It resolves a connector's credentials exactly as the API does: the durable
    tenant-scoped secret store (Postgres ``mcp_credentials``, Redis as the
    ciphertext cache) through the tenant-aware resolver, and OAuth connections
    through a DB-backed OAuth manager that reads ``oauth_tokens`` and refreshes an
    expired access token with the stored refresh token. The workflow worker used
    to assemble its own client without the OAuth manager, so a workflow tool step
    on an OAuth connector never had a token (and an expired one was never
    refreshed), while the goal worker's identical call worked.
    """
    import logging

    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPRegistry
    from app.providers.vault import resolve_connector_secret_ref_for_tenant

    factory = db_factory if db_factory is not None else _default_db_factory()
    if register_builtin_handlers:
        # Python handlers do not survive the registry's JSON round-trip: a fresh
        # worker process must register them itself.
        from app.mcp.servers.registry_wiring import get_builtin_server_configs

        for cfg in get_builtin_server_configs():
            if cfg.get("handler") is not None:
                MCPRegistry.register_builtin_handler(cfg["server_id"], cfg["handler"])
    secret_store = build_connector_secret_store(redis, db_factory=factory)

    async def _resolve_secret(ref: str, tenant_ctx: Any = None) -> str | None:
        return await resolve_connector_secret_ref_for_tenant(
            ref, store=secret_store, tenant_ctx=tenant_ctx
        )

    client = MCPClient(
        build_connector_registry(redis, db_factory=factory),
        secret_resolver=_resolve_secret,
        redis=redis,
        llm_provider=llm_provider,
    )
    try:
        from app.mcp.oauth import build_worker_oauth_manager

        client._oauth_manager = build_worker_oauth_manager(factory, redis=redis)
    except Exception as exc:
        logging.getLogger(__name__).warning("worker_oauth_manager_wire_failed: %s", exc)
    return client


__all__ = ["build_connector_registry", "build_connector_secret_store", "build_worker_mcp_client"]
