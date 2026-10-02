"""MCP server registry — Postgres source of truth, Redis read cache.

Durable mode (``db_factory`` set — the API lifespan and the Celery workers):
connectors live in the tenant-scoped, RLS-protected ``mcp_servers`` table
(``app.mcp.connector_store``); Redis keeps a short-TTL cache under
``mcp:cfgcache:v1:{tenant_id}:{server_id}`` that every write invalidates.

Redis-only mode (no ``db_factory``: unit tests and the in-memory app before the
lifespan swap) keeps the original layout, which is also the LEGACY layout the
one-time backfill copies from:
  mcp:servers:{tenant_id}:{server_id}  -> JSON-encoded MCPServerConfig
  mcp:server_ids:{tenant_id}           -> Redis set of server_id strings
"""

from __future__ import annotations

import enum
import logging
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tenancy.context import TenantContext

_log = logging.getLogger(__name__)

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
    # Per-connector opt-in: when True, this connector's high-risk (write_high)
    # tools are auto-approved in autonomous (non-supervised) goals instead of
    # stalling on human approval. Explicit, scoped consent — default-secure OFF.
    auto_approve: bool = False
    # Canonical built-in this connection dispatches to (e.g. "builtin-mongodb").
    # The handler is looked up by TYPE, not by server_id, so a tenant can hold
    # several connections of one type ("builtin-mongodb:orders-db",
    # "builtin-mongodb:analytics-db"), each with its own credentials, and the
    # lookup survives a restart. Older rows stored under the canonical id itself
    # ("builtin-mongodb") infer it from that id.
    builtin_type: str = ""
    # Callable for built-in server dispatch — excluded from JSON serialization
    builtin_handler: Any = Field(default=None, exclude=True)
    # Transport: "http" (default) | "ws" | "websocket"
    transport: str = "http"
    # WebSocket URL when transport is "ws" or "websocket"
    ws_url: str | None = None

    @model_validator(mode="after")
    def _sync_url_fields(self) -> MCPServerConfig:
        """Keep url and base_url in sync so either can be used."""
        if self.base_url and not self.url:
            self.url = self.base_url
        elif self.url and not self.base_url:
            self.base_url = self.url
        if not self.builtin_type and self.server_id.startswith("builtin-"):
            # Compat: legacy "builtin-<name>" ids and "builtin-<type>:<slug>" ids.
            self.builtin_type = self.server_id.split(":", 1)[0]
        return self


class MCPRegistry:
    """Per-tenant registry of MCP servers.

    With ``db_factory`` (API lifespan, Celery workers) Postgres ``mcp_servers`` is
    the source of truth and Redis only a short-TTL read cache that every write
    invalidates (the cache is the shared Redis, so the invalidation reaches every
    replica): a Redis FLUSHALL or eviction loses nothing. Without it (unit tests,
    the pre-lifespan in-memory app) the registry is Redis-only, as before.

    Args:
        redis: Any Redis-compatible async client (accepts Any to avoid import coupling).
        db_factory: async SQLAlchemy session factory (the durable store).
    """

    def __init__(
        self,
        redis: Any,
        *,
        auto_provision_builtins: bool = False,
        db_factory: Any = None,
        cache_ttl_s: int = 60,
    ) -> None:
        self._redis = redis
        # Production registries (API lifespan, Celery workers) set this so a tenant
        # created after startup (signup, SSO JIT) still gets the credential-free
        # built-ins the lifespan only wired for tenants that existed at boot.
        self._auto_provision_builtins = auto_provision_builtins
        self._cache_ttl_s = cache_ttl_s
        self._rows: Any = None
        self.db_factory: Any = None
        self._backfill: Any = None
        if db_factory is not None:
            self.set_db(db_factory)

    _KEEP: Any = object()

    def set_db(self, db_factory: Any, *, cache: Any = _KEEP) -> None:
        """Make Postgres the source of truth (two-phase wiring in the lifespan).

        ``cache`` replaces the Redis client (the shared Redis, or ``None`` for no
        cache at all — never an in-process stand-in shared by nobody else).
        """
        from app.mcp.connector_store import BackfillState, PostgresConnectorRows

        if cache is not MCPRegistry._KEEP:
            self._redis = cache
        self.db_factory = db_factory
        self._rows = PostgresConnectorRows(db_factory)
        self._backfill = BackfillState(db_factory)

    @property
    def durable(self) -> bool:
        return self._rows is not None

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

    def _builtins_marker_key(self, tenant_id: str) -> str:
        return f"mcp:builtins_provisioned:{tenant_id}"

    def _cache_key(self, tenant_id: str, server_id: str) -> str:
        return f"mcp:cfgcache:v1:{tenant_id}:{server_id}"

    # ── cache helpers (durable mode): a Redis error never fails a request ────

    async def _cache_get(self, tenant_id: str, server_id: str) -> str | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._cache_key(tenant_id, server_id))
        except Exception as exc:
            _log.warning("connector_cache_read_failed tenant=%s error=%s", tenant_id, exc)
            return None
        return _text(raw)

    async def _cache_set(self, tenant_id: str, server_id: str, value: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                self._cache_key(tenant_id, server_id), value, ex=self._cache_ttl_s
            )
        except Exception as exc:
            _log.warning("connector_cache_write_failed tenant=%s error=%s", tenant_id, exc)

    async def _cache_invalidate(self, tenant_id: str, server_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(self._cache_key(tenant_id, server_id))
        except Exception as exc:
            # Bounded by the cache TTL; logged so a stale window is visible.
            _log.warning("connector_cache_invalidate_failed tenant=%s error=%s", tenant_id, exc)

    async def _legacy_reads(self) -> bool:
        return self._redis is not None and bool(await self._backfill.legacy_reads_needed())

    async def _read_legacy(self, tenant_id: str, server_id: str) -> str | None:
        try:
            raw = await self._redis.get(self._server_key(tenant_id, server_id))
        except Exception as exc:
            _log.warning("legacy_connector_read_failed tenant=%s error=%s", tenant_id, exc)
            return None
        return _text(raw)

    async def _get_builtins_marker(self, tenant_id: str) -> str | None:
        """The provisioning marker (Postgres in durable mode; adopts a legacy one)."""
        import json

        if self._rows is None:
            return _text(await self._redis.get(self._builtins_marker_key(tenant_id)))
        marker = await self._rows.get_builtins_marker(tenant_id)
        if marker is not None:
            return json.dumps(marker)
        if self._redis is None:
            return None
        try:
            return _text(await self._redis.get(self._builtins_marker_key(tenant_id)))
        except Exception:
            return None

    async def _set_builtins_marker(self, tenant_id: str, fingerprint: str, ids: list[str]) -> None:
        import json

        if self._rows is None:
            await self._redis.set(
                self._builtins_marker_key(tenant_id), json.dumps({"fp": fingerprint, "ids": ids})
            )
            return
        await self._rows.set_builtins_marker(tenant_id, fingerprint, ids)

    async def _ensure_builtins(self, tenant_ctx: TenantContext) -> None:
        """Insert-if-absent the credential-free built-ins once per tenant.

        The marker lives in the shared store (Postgres in durable mode), so it
        holds across replicas, workers and a Redis flush, and a built-in the
        tenant later removes is not re-added here.
        It records the built-in catalogue fingerprint and the credential-free
        ids provisioned: when a release changes the catalogue, the tenant's next
        listing refreshes its built-in tool lists and inserts only built-ins
        that are NEW since then (what the removed per-tenant startup loop used
        to do on every boot, for every tenant). A failure is logged and retried
        on the next listing (marker unchanged).
        """
        import json

        from app.mcp.servers.registry_wiring import (
            builtin_catalog_fingerprint,
            credential_free_builtin_ids,
            register_builtin_servers,
        )

        raw = await self._get_builtins_marker(tenant_ctx.tenant_id)
        fingerprint = builtin_catalog_fingerprint()
        insert_ids: set[str] | None = None  # never provisioned: insert all absent
        current_ids = credential_free_builtin_ids()
        if raw:
            try:
                state = json.loads(raw)
            except ValueError:
                state = None
            if isinstance(state, dict):
                if state.get("fp") == fingerprint:
                    return
                insert_ids = current_ids - {str(i) for i in state.get("ids") or []}
            else:
                # Legacy marker ("1"): provisioned before fingerprints existed.
                # Refresh the tool lists; insert nothing (it may have been removed).
                insert_ids = set()

        try:
            await register_builtin_servers(self, tenant_ctx, insert_ids=insert_ids)
        except Exception as exc:
            _log.warning(
                "builtin_lazy_provision_failed tenant=%s error=%s", tenant_ctx.tenant_id, exc
            )
            return
        await self._set_builtins_marker(tenant_ctx.tenant_id, fingerprint, sorted(current_ids))

    @staticmethod
    def _resolve(config: MCPServerConfig | Callable[[str], MCPServerConfig]) -> MCPServerConfig:
        if callable(config):
            generated_id = uuid.uuid4().hex
            resolved = config(generated_id)
            if not resolved.server_id:
                resolved.server_id = generated_id
            return resolved
        if not config.server_id:
            config.server_id = uuid.uuid4().hex
        return config

    async def register(
        self,
        config: MCPServerConfig | Callable[[str], MCPServerConfig],
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        """Register (insert or replace) an MCP server and return its ID.

        If config.server_id is already set (non-empty), that value is used as
        the registry key so callers can pre-specify stable IDs (e.g. built-ins).
        In durable mode a display name another connector of the tenant already
        uses raises :class:`~app.mcp.connector_store.ConnectorConflictError`.
        """
        resolved_config = self._resolve(config)
        server_id = resolved_config.server_id
        tid = tenant_ctx.tenant_id
        if self._rows is not None:
            await self._rows.write(
                tid, server_id, resolved_config.model_dump(mode="json"), mode="upsert"
            )
            await self._cache_invalidate(tid, server_id)
            return server_id
        await self._redis.set(self._server_key(tid, server_id), resolved_config.model_dump_json())
        await self._redis.sadd(self._index_key(tid), server_id)
        return server_id

    async def create(
        self,
        config: MCPServerConfig | Callable[[str], MCPServerConfig],
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        """Insert a NEW server; never replaces one (atomic create).

        Raises :class:`~app.mcp.connector_store.ConnectorConflictError` when the
        id (``kind="id"``) or, in durable mode, the tenant-unique display name
        (``kind="name"``) is taken.
        """
        from app.mcp.connector_store import ConnectorConflictError

        resolved_config = self._resolve(config)
        server_id = resolved_config.server_id
        tid = tenant_ctx.tenant_id
        if self._rows is not None:
            await self._rows.write(
                tid, server_id, resolved_config.model_dump(mode="json"), mode="insert"
            )
            await self._cache_invalidate(tid, server_id)
            return server_id
        key = self._server_key(tid, server_id)
        try:
            created = await self._redis.set(key, resolved_config.model_dump_json(), nx=True)
        except TypeError:
            # Minimal duck-typed clients (test doubles) without SET NX: best effort.
            if await self._redis.get(key) is not None:
                created = None
            else:
                await self._redis.set(key, resolved_config.model_dump_json())
                created = True
        if not created:
            raise ConnectorConflictError("id", f"A connector with id '{server_id}' already exists")
        await self._redis.sadd(self._index_key(tid), server_id)
        return server_id

    @staticmethod
    def _attach_handler(cfg: MCPServerConfig, server_id: str) -> MCPServerConfig:
        # Re-attach the process-local built-in handler (lost on serialisation) —
        # by the connection's built-in TYPE first, so every connection of one
        # type resolves the same handler after a restart.
        handler = (
            _BUILTIN_HANDLER_REGISTRY.get(cfg.builtin_type) if cfg.builtin_type else None
        ) or _BUILTIN_HANDLER_REGISTRY.get(server_id)
        if handler is not None:
            cfg.builtin_handler = handler
        return cfg

    async def get(self, server_id: str, *, tenant_ctx: TenantContext) -> MCPServerConfig | None:
        """Fetch a server config by ID; returns None if not found or cross-tenant.

        Re-attaches the process-local built-in handler after deserializing,
        because ``builtin_handler`` is excluded from JSON serialization.
        """
        tid = tenant_ctx.tenant_id
        if self._rows is None:
            raw = _text(await self._redis.get(self._server_key(tid, server_id)))
            if raw is None:
                return None
            return self._attach_handler(MCPServerConfig.model_validate_json(raw), server_id)

        cached = await self._cache_get(tid, server_id)
        if cached is not None:
            return self._attach_handler(MCPServerConfig.model_validate_json(cached), server_id)
        data = await self._rows.get(tid, server_id)
        if data is None and await self._legacy_reads():
            legacy = await self._read_legacy(tid, server_id)
            if legacy is not None:
                from app.mcp.connector_store import copy_legacy_server

                await copy_legacy_server(self._rows, tid, server_id, legacy)
                data = await self._rows.get(tid, server_id)
        if data is None:
            return None
        cfg = MCPServerConfig.model_validate(data)
        await self._cache_set(tid, server_id, cfg.model_dump_json())
        return self._attach_handler(cfg, server_id)

    async def list_servers(self, *, tenant_ctx: TenantContext) -> list[MCPServerConfig]:
        """Return all servers registered for this tenant."""
        return [cfg for _, cfg in await self.list_server_records(tenant_ctx=tenant_ctx)]

    async def list_page(
        self, *, tenant_ctx: TenantContext, limit: int = 100, after: str | None = None
    ) -> list[tuple[str, MCPServerConfig]]:
        """One keyset page (ordered by id, ``after`` = last id seen) of the servers."""
        if self._rows is None or await self._legacy_reads():
            ordered = sorted(
                await self.list_server_records(tenant_ctx=tenant_ctx), key=lambda r: r[0]
            )
            return [r for r in ordered if after is None or r[0] > after][:limit]
        if self._auto_provision_builtins:
            await self._ensure_builtins(tenant_ctx)
        page = await self._rows.list_page(tenant_ctx.tenant_id, limit=limit, after=after)
        return [
            (sid, self._attach_handler(MCPServerConfig.model_validate(data), sid))
            for sid, data in page
        ]

    async def list_server_records(
        self, *, tenant_ctx: TenantContext
    ) -> list[tuple[str, MCPServerConfig]]:
        """Return all servers with their registry IDs for this tenant."""
        if self._auto_provision_builtins:
            await self._ensure_builtins(tenant_ctx)
        tid = tenant_ctx.tenant_id
        if self._rows is not None:
            rows = await self._rows.list_all(tid)
            if await self._legacy_reads():
                rows = await self._merge_legacy(tid, rows)
            return [
                (sid, self._attach_handler(MCPServerConfig.model_validate(data), sid))
                for sid, data in rows
            ]
        ids: set[str] = await self._redis.smembers(self._index_key(tid))
        sids = sorted(i.decode() if isinstance(i, bytes) else str(i) for i in ids)
        if not sids:
            return []
        # One MGET round-trip instead of a GET per connector (MCPREG-06).
        keys = [self._server_key(tid, sid) for sid in sids]
        mget = getattr(self._redis, "mget", None)
        if mget is not None:
            raws = list(await mget(keys))
        else:  # minimal duck-typed clients (test doubles) without MGET
            raws = [await self._redis.get(k) for k in keys]
        servers: list[tuple[str, MCPServerConfig]] = []
        for sid, raw in zip(sids, raws, strict=True):
            text_raw = _text(raw)
            if text_raw is None:
                continue  # dangling index entry
            cfg = MCPServerConfig.model_validate_json(text_raw)
            servers.append((sid, self._attach_handler(cfg, sid)))
        return servers

    async def _merge_legacy(
        self, tenant_id: str, rows: list[tuple[str, dict[str, Any]]]
    ) -> list[tuple[str, dict[str, Any]]]:
        """Read-repair: copy legacy Redis-only connectors into Postgres."""
        from app.mcp.connector_store import copy_legacy_server

        try:
            ids = await self._redis.smembers(self._index_key(tenant_id))
        except Exception as exc:
            _log.warning("legacy_connector_index_read_failed tenant=%s error=%s", tenant_id, exc)
            return rows
        present = {sid for sid, _ in rows}
        missing = sorted(
            sid
            for sid in (i.decode() if isinstance(i, bytes) else str(i) for i in ids)
            if sid not in present
        )
        if not missing:
            return rows
        for sid in missing:
            legacy = await self._read_legacy(tenant_id, sid)
            if legacy is not None:
                await copy_legacy_server(self._rows, tenant_id, sid, legacy)
        return list(await self._rows.list_all(tenant_id))

    async def unregister(self, server_id: str, *, tenant_ctx: TenantContext) -> bool:
        """Remove a server; returns True if it existed and was removed."""
        tid = tenant_ctx.tenant_id
        if self._rows is not None:
            deleted_row = bool(await self._rows.delete(tid, server_id))
            await self._cache_invalidate(tid, server_id)
            # Drop the legacy Redis copy too, so a re-run backfill or the
            # read-repair can never resurrect a connector the tenant deleted.
            legacy_deleted = 0
            if self._redis is not None:
                try:
                    legacy_deleted = int(
                        await self._redis.delete(self._server_key(tid, server_id)) or 0
                    )
                    await self._redis.srem(self._index_key(tid), server_id)
                except Exception as exc:
                    _log.warning("legacy_connector_delete_failed tenant=%s error=%s", tid, exc)
            return deleted_row or bool(legacy_deleted)
        key = self._server_key(tid, server_id)
        deleted: int = await self._redis.delete(key)
        if deleted == 0:
            return False
        await self._redis.srem(self._index_key(tid), server_id)
        return True

    async def update(
        self, server_id: str, config: MCPServerConfig, *, tenant_ctx: TenantContext
    ) -> bool:
        """Replace a registered server config while preserving its server ID."""
        tid = tenant_ctx.tenant_id
        if self._rows is not None:
            if await self.get(server_id, tenant_ctx=tenant_ctx) is None:
                return False
            data = config.model_dump(mode="json")
            data["server_id"] = server_id
            updated = bool(await self._rows.write(tid, server_id, data, mode="update"))
            await self._cache_invalidate(tid, server_id)
            return updated
        key = self._server_key(tid, server_id)
        if await self._redis.get(key) is None:
            return False
        await self._redis.set(key, config.model_dump_json())
        await self._redis.sadd(self._index_key(tid), server_id)
        return True

    # Alias — preferred name for callers that scan all registered servers
    async def list_all(self, *, tenant_ctx: TenantContext) -> list[MCPServerConfig]:
        """Alias for list_servers()."""
        return await self.list_servers(tenant_ctx=tenant_ctx)


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)
