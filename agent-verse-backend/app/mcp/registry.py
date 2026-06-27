"""Redis-backed MCP server registry.

Servers are registered per-tenant with a UUID server ID. All Redis keys are
namespaced as:
  mcp:servers:{tenant_id}:{server_id}  → JSON-encoded MCPServerConfig
  mcp:server_ids:{tenant_id}           → Redis set of server_id strings

This allows no-restart register/unregister and per-tenant isolation without
any shared mutable state.
"""
from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tenancy.context import TenantContext

# Module-level dict — process-local, not serialized to Redis.
# Holds callable handlers for built-in servers so they survive Redis round-trips.
_BUILTIN_HANDLER_REGISTRY: dict[str, Any] = {}


class AuthType(enum.StrEnum):
    BEARER = "bearer"
    API_KEY = "api_key"
    OAUTH_AC = "oauth_ac"
    OAUTH_CC = "oauth_cc"
    PKCE = "pkce"
    BASIC = "basic"
    CUSTOM_HEADER = "custom_header"
    MTLS = "mtls"
    HMAC = "hmac"
    NONE = "none"


class ServerStatus(enum.StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    UNHEALTHY = "unhealthy"
    REMOVED = "removed"


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Pre-set or auto-generated server identity
    server_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    # url: existing field kept for backward compat; base_url is the new preferred name
    url: str = ""
    base_url: str = ""
    auth_type: AuthType = AuthType.NONE
    auth_config: dict[str, Any] = Field(default_factory=dict)
    status: ServerStatus = ServerStatus.ACTIVE
    description: str = ""
    priority: int = 0
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list)
    tool_definitions: list[dict[str, Any]] = Field(default_factory=list)
    # Callable for built-in server dispatch — excluded from JSON serialization
    builtin_handler: Any = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _sync_url_fields(self) -> "MCPServerConfig":
        """Keep url and base_url in sync so either can be used."""
        if self.base_url and not self.url:
            self.url = self.base_url
        elif self.url and not self.base_url:
            self.base_url = self.url
        return self


class MCPRegistry:
    """Per-tenant registry of MCP servers stored in Redis.

    Args:
        redis: Any Redis-compatible async client (accepts Any to avoid import coupling).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    @staticmethod
    def register_builtin_handler(server_id: str, handler: Any) -> None:
        """Register a handler callable for a built-in server. Process-local only."""
        _BUILTIN_HANDLER_REGISTRY[server_id] = handler

    @staticmethod
    def get_builtin_handler(server_id: str) -> Any | None:
        """Return the process-local built-in handler for server_id, or None."""
        return _BUILTIN_HANDLER_REGISTRY.get(server_id)

    def _server_key(self, tenant_id: str, server_id: str) -> str:
        return f"mcp:servers:{tenant_id}:{server_id}"

    def _index_key(self, tenant_id: str) -> str:
        return f"mcp:server_ids:{tenant_id}"

    async def register(
        self,
        config: MCPServerConfig | Callable[[str], MCPServerConfig],
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        """Register a new MCP server and return its ID.

        If config.server_id is already set (non-empty), that value is used as
        the registry key so callers can pre-specify stable IDs (e.g. built-ins).
        """
        if callable(config):
            generated_id = uuid.uuid4().hex
            resolved_config = config(generated_id)
            server_id = resolved_config.server_id or generated_id
        else:
            resolved_config = config
            server_id = resolved_config.server_id or uuid.uuid4().hex

        key = self._server_key(tenant_ctx.tenant_id, server_id)
        await self._redis.set(key, resolved_config.model_dump_json())
        await self._redis.sadd(self._index_key(tenant_ctx.tenant_id), server_id)
        return server_id

    async def get(self, server_id: str, *, tenant_ctx: TenantContext) -> MCPServerConfig | None:
        """Fetch a server config by ID; returns None if not found or cross-tenant.

        Re-attaches the process-local built-in handler after deserializing from
        Redis, because ``builtin_handler`` is excluded from JSON serialization.
        """
        key = self._server_key(tenant_ctx.tenant_id, server_id)
        raw: str | None = await self._redis.get(key)
        if raw is None:
            return None
        cfg = MCPServerConfig.model_validate_json(raw)
        # Re-attach builtin handler from process-local registry (lost on Redis round-trip)
        handler = _BUILTIN_HANDLER_REGISTRY.get(server_id)
        if handler is not None:
            cfg.builtin_handler = handler
        return cfg

    async def list_servers(self, *, tenant_ctx: TenantContext) -> list[MCPServerConfig]:
        """Return all servers registered for this tenant."""
        return [cfg for _, cfg in await self.list_server_records(tenant_ctx=tenant_ctx)]

    async def list_server_records(
        self, *, tenant_ctx: TenantContext
    ) -> list[tuple[str, MCPServerConfig]]:
        """Return all servers with their registry IDs for this tenant."""
        ids: set[str] = await self._redis.smembers(self._index_key(tenant_ctx.tenant_id))
        servers: list[tuple[str, MCPServerConfig]] = []
        for server_id in ids:
            sid = server_id.decode() if isinstance(server_id, bytes) else str(server_id)
            cfg = await self.get(sid, tenant_ctx=tenant_ctx)
            if cfg is not None:
                servers.append((sid, cfg))
        return servers

    async def unregister(self, server_id: str, *, tenant_ctx: TenantContext) -> bool:
        """Remove a server; returns True if it existed and was removed."""
        key = self._server_key(tenant_ctx.tenant_id, server_id)
        deleted: int = await self._redis.delete(key)
        if deleted == 0:
            return False
        await self._redis.srem(self._index_key(tenant_ctx.tenant_id), server_id)
        return True

    async def update(
        self, server_id: str, config: MCPServerConfig, *, tenant_ctx: TenantContext
    ) -> bool:
        """Replace a registered server config while preserving its server ID."""
        key = self._server_key(tenant_ctx.tenant_id, server_id)
        if await self._redis.get(key) is None:
            return False
        await self._redis.set(key, config.model_dump_json())
        await self._redis.sadd(self._index_key(tenant_ctx.tenant_id), server_id)
        return True

    # Alias — preferred name for callers that scan all registered servers
    async def list_all(self, *, tenant_ctx: TenantContext) -> list[MCPServerConfig]:
        """Alias for list_servers()."""
        return await self.list_servers(tenant_ctx=tenant_ctx)
