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
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

AuthType = Literal[
    "bearer",
    "api_key",
    "oauth_ac",
    "oauth_cc",
    "pkce",
    "basic",
    "custom_header",
    "mtls",
    "hmac",
]


class ServerStatus(enum.StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    UNHEALTHY = "unhealthy"
    REMOVED = "removed"


class MCPServerConfig(BaseModel):
    name: str
    url: str
    auth_type: AuthType
    auth_config: dict[str, Any] = Field(default_factory=dict)
    status: ServerStatus = ServerStatus.ACTIVE
    description: str = ""
    priority: int = 0


class MCPRegistry:
    """Per-tenant registry of MCP servers stored in Redis.

    Args:
        redis: Any Redis-compatible async client (accepts Any to avoid import coupling).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def _server_key(self, tenant_id: str, server_id: str) -> str:
        return f"mcp:servers:{tenant_id}:{server_id}"

    def _index_key(self, tenant_id: str) -> str:
        return f"mcp:server_ids:{tenant_id}"

    async def register(self, config: MCPServerConfig, *, tenant_ctx: TenantContext) -> str:
        """Register a new MCP server and return its ID."""
        server_id = uuid.uuid4().hex
        key = self._server_key(tenant_ctx.tenant_id, server_id)
        await self._redis.set(key, config.model_dump_json())
        await self._redis.sadd(self._index_key(tenant_ctx.tenant_id), server_id)
        return server_id

    async def get(self, server_id: str, *, tenant_ctx: TenantContext) -> MCPServerConfig | None:
        """Fetch a server config by ID; returns None if not found or cross-tenant."""
        key = self._server_key(tenant_ctx.tenant_id, server_id)
        raw: str | None = await self._redis.get(key)
        if raw is None:
            return None
        return MCPServerConfig.model_validate_json(raw)

    async def list_servers(self, *, tenant_ctx: TenantContext) -> list[MCPServerConfig]:
        """Return all servers registered for this tenant."""
        ids: set[str] = await self._redis.smembers(self._index_key(tenant_ctx.tenant_id))
        servers: list[MCPServerConfig] = []
        for server_id in ids:
            cfg = await self.get(server_id, tenant_ctx=tenant_ctx)
            if cfg is not None:
                servers.append(cfg)
        return servers

    async def unregister(self, server_id: str, *, tenant_ctx: TenantContext) -> bool:
        """Remove a server; returns True if it existed and was removed."""
        key = self._server_key(tenant_ctx.tenant_id, server_id)
        deleted: int = await self._redis.delete(key)
        if deleted == 0:
            return False
        await self._redis.srem(self._index_key(tenant_ctx.tenant_id), server_id)
        return True
