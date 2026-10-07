"""Connectors API — register, list, and manage MCP server connections."""

from __future__ import annotations

import base64
import inspect
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.mcp.catalog import CONNECTOR_CATALOG
from app.mcp.connector_store import ConnectorConflictError
from app.mcp.dsn_secrets import is_dsn, mask_dsn, seal_connector_dsns
from app.mcp.oauth import (
    OAuthExchangeError,
    OAuthFlowManager,
    OAuthInvalidStateError,
    OAuthTokenPersistError,
)
from app.mcp.registry import AuthType, MCPRegistry, MCPServerConfig
from app.net.ssrf_guard import (
    SSRFError,
    assert_public_url_async,
    private_access_networks,
    public_async_client,
)
from app.providers.vault import (
    ConnectorSecretUnavailableError,
    ConnectorSecretUndecryptableError,
    connector_secret_ref,
    is_connector_secret_ref,
    resolve_connector_secret_ref,
    resolve_connector_secret_ref_for_tenant,
    store_connector_secret_for_tenant,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])
_REDACTED = "<redacted>"
_logger = logging.getLogger(__name__)
_SENSITIVE_AUTH_KEY_PARTS = {
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}

def _builtin_config_for_type(value: str) -> dict[str, Any] | None:
    """The built-in server config for a declared type / canonical id, or None."""
    try:
        from app.mcp.servers.registry_wiring import builtin_config_for_type

        return builtin_config_for_type(value)
    except Exception:
        return None


_CONNECTOR_TYPE_CACHE: dict[str, str] | None = None


def _connector_type_for(builtin_type: str) -> str:
    """The connector-catalog type key for a built-in ('builtin-mongodb' -> 'mongodb')."""
    global _CONNECTOR_TYPE_CACHE
    if _CONNECTOR_TYPE_CACHE is None:
        mapping: dict[str, str] = {}
        for spec in CONNECTOR_CATALOG:
            cfg = _builtin_config_for_type(spec.builtin_server_id or spec.name)
            if cfg is not None:
                mapping.setdefault(str(cfg["server_id"]), spec.name)
        _CONNECTOR_TYPE_CACHE = mapping
    return _CONNECTOR_TYPE_CACHE.get(builtin_type) or builtin_type.removeprefix("builtin-")


def _declared_type_on_update(body: RegisterConnectorRequest) -> str:
    if not body.builtin_type:
        return ""
    cfg = _builtin_config_for_type(body.builtin_type)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown connector type '{body.builtin_type}'",
        )
    return str(cfg["server_id"])


def _assert_builtin_enabled(builtin_type: str) -> None:
    """422 when the operator switched this built-in connector type off (NF-13)."""
    from app.mcp.builtin_kill_switch import disabled_reason

    reason = disabled_reason(builtin_type)
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=reason)


def _infer_builtin_type(name: str, url: str = "") -> str | None:
    """The built-in type a connector NAME implies (``None`` when it implies none).

    A connector whose URL is a remote MCP endpoint IS that MCP server: only an
    exact built-in name ("Jira") selects the built-in there. The leading-token
    match ("mongodb-prod") must not turn "Jira MCP" at ``https://…/mcp`` into the
    built-in Jira REST handler — its calls would no longer reach the MCP server
    the user registered.
    """
    try:
        from app.mcp.servers.registry_wiring import (
            builtin_config_for_type,
            infer_builtin_type_from_name,
        )

        if url and _is_mcp_endpoint(url):
            exact = builtin_config_for_type(name)
            return str(exact["server_id"]) if exact is not None else None
        return infer_builtin_type_from_name(name)
    except Exception:
        return None


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _connection_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "connection"


async def _assert_unique_name(
    reg: Any, name: str, *, tenant_ctx: Any, exclude_id: str | None = None
) -> None:
    """409 when another connector of this tenant already uses ``name``."""
    if not hasattr(reg, "list_server_records"):
        return
    wanted = _name_key(name)
    for sid, cfg in await reg.list_server_records(tenant_ctx=tenant_ctx):
        if sid != exclude_id and _name_key(cfg.name) == wanted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A connector named '{name}' already exists (server_id {sid}). "
                    "Connector names must be unique; choose a different name."
                ),
            )


async def _new_connection_id(reg: Any, canonical: str, name: str, *, tenant_ctx: Any) -> str:
    """A distinct id per connection of a built-in type: builtin-<type>:<slug>."""
    candidate = f"{canonical}:{_connection_slug(name)}"
    getter = getattr(reg, "get", None)
    try:
        taken = getter is not None and await getter(candidate, tenant_ctx=tenant_ctx) is not None
    except Exception:
        taken = False
    return f"{candidate}-{uuid.uuid4().hex[:6]}" if taken else candidate


async def _create_connector(
    reg: Any,
    config_for: Callable[[str], MCPServerConfig],
    *,
    tenant_ctx: Any,
    name: str,
    pending_secrets: dict[str, str],
    on_id_conflict: Callable[[], None] | None = None,
) -> str:
    """Atomic create (MCPREG-07): an insert, never an unconditional upsert.

    A display name taken concurrently is a 409 (the durable store's unique
    (tenant, name) index decides); a connection id taken concurrently is retried
    after ``on_id_conflict`` picks a new one (a generated id is fresh anyway).
    """
    create = getattr(reg, "create", None) or reg.register
    for attempt in range(3):
        pending_secrets.clear()
        try:
            return str(await create(config_for, tenant_ctx=tenant_ctx))
        except ConnectorConflictError as exc:
            if exc.kind == "name" or attempt == 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"A connector named '{name}' already exists. "
                        "Connector names must be unique; choose a different name."
                    ),
                ) from exc
            if on_id_conflict is not None:
                on_id_conflict()
    raise AssertionError("unreachable")  # pragma: no cover


class RegisterConnectorRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    url: str
    # Declared built-in type ("mongodb", "MongoDB", "google_sheets" or
    # "builtin-mongodb"); the connectors UI sends it as "connector_type". When
    # omitted it is inferred from the name, but only when that is unambiguous
    # ("MongoDB", "mongodb-prod"); otherwise the connector is a plain remote MCP
    # server. Several connections may share one type.
    builtin_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("type", "connector_type", "builtin_type"),
    )
    # The enum, not ``str``: an unknown value used to pass request validation and
    # then blow up constructing MCPServerConfig inside the registry — an
    # unhandled pydantic error, i.e. HTTP 500 on ordinary bad input. Now a 422
    # that names the accepted values, which the schema also advertises.
    auth_type: AuthType
    auth_config: dict[str, Any] = {}
    description: str = ""
    priority: int = 0
    # When True, this connector's high-risk tools run without human approval in
    # autonomous goals (explicit per-connector opt-in; default-secure OFF).
    auto_approve: bool = False


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _registry(request: Request) -> Any:
    from app.api._deps import get_mcp_registry as _gmr

    return _gmr(request)


def _is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "development").lower() == "production"


def _normalize_auth_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_auth_key(key: str) -> bool:
    normalized = _normalize_auth_key(key)
    return any(part in normalized for part in _SENSITIVE_AUTH_KEY_PARTS)


def _auth_config_requires_secret_storage(auth_config: dict[str, Any], url: str = "") -> bool:
    # A database connection string carries its password in the userinfo: it is a
    # secret whatever key holds it (MDB-01).
    return is_dsn(url) or any(
        (_is_sensitive_auth_key(key) or is_dsn(value))
        and value not in (None, "", _REDACTED)
        and not is_connector_secret_ref(value)
        for key, value in auth_config.items()
    )


def _connector_secret_store(
    request: Request,
    *,
    needs_secret_storage: bool = False,
) -> Any:
    store = getattr(request.app.state, "connector_secret_store", None)
    production_safe = bool(
        getattr(request.app.state, "connector_secret_store_is_production_safe", False)
    ) or bool(getattr(store, "production_safe", False))
    if needs_secret_storage and _is_production() and not production_safe:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "production connector secret storage is not configured; refusing to "
                "store connector secrets in the in-memory process-local store"
            ),
        )
    if store is None:
        store = {}
        request.app.state.connector_secret_store = store
    return store


def _secret_resolver(request: Request) -> Callable[..., Any]:
    tenant_ctx = _require_tenant(request)

    async def _resolve(ref: str, resolver_tenant_ctx: Any = None) -> str | None:
        store = _connector_secret_store(request)
        return await resolve_connector_secret_ref_for_tenant(
            ref,
            store=store,
            tenant_ctx=resolver_tenant_ctx or tenant_ctx,
        )

    return _resolve


async def _resolve_auth_value(
    value: Any,
    secret_resolver: Callable[..., Any] | None,
) -> str:
    if is_connector_secret_ref(value):
        if secret_resolver is None:
            return resolve_connector_secret_ref(value)  # raises: no process fallback
        resolved = secret_resolver(value)
        if hasattr(resolved, "__await__"):
            resolved = await resolved
        if not resolved:
            # Never authenticate with an empty secret (PROV-14).
            raise ConnectorSecretUnavailableError(
                f"connector secret {value!r} could not be resolved"
            )
        return str(resolved)
    return str(value)


async def _build_auth_headers(
    cfg: MCPServerConfig,
    *,
    secret_resolver: Callable[..., Any] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    auth = cfg.auth_config
    if cfg.auth_type == "bearer":
        token = await _resolve_auth_value(auth.get("token", ""), secret_resolver)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif cfg.auth_type == "api_key":
        key_name = str(auth.get("header_name", "X-API-Key"))
        key_value = await _resolve_auth_value(auth.get("api_key", ""), secret_resolver)
        if key_value:
            headers[key_name] = key_value
    elif cfg.auth_type == "basic":
        username = str(auth.get("username", ""))
        password = await _resolve_auth_value(auth.get("password", ""), secret_resolver)
        if username:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
    elif cfg.auth_type == "custom_header":
        for key, value in auth.items():
            headers[key] = await _resolve_auth_value(value, secret_resolver)
    return headers


def _is_mcp_endpoint(url: str) -> bool:
    path = url.split("?", 1)[0].rstrip("/")
    return path.endswith("/mcp") or path.endswith("/mcp/authv2")


def _mask_auth_config(auth_config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _REDACTED
        if _is_sensitive_auth_key(key) or is_connector_secret_ref(value) or is_dsn(value)
        else value
        for key, value in auth_config.items()
    }


def _store_sensitive_auth_refs(
    server_id: str,
    auth_config: dict[str, Any],
    pending_secrets: dict[str, str],
) -> dict[str, Any]:
    stored = dict(auth_config)
    for key, value in auth_config.items():
        if not _is_sensitive_auth_key(key):
            continue
        if value in (None, "", _REDACTED) or is_connector_secret_ref(value):
            continue
        ref = connector_secret_ref(server_id, key)
        pending_secrets[ref] = str(value)
        stored[key] = ref
    return stored


async def _persist_connector_secrets(
    pending_secrets: dict[str, str],
    *,
    secret_store: Any,
    tenant_ctx: Any,
) -> None:
    for ref, value in pending_secrets.items():
        await store_connector_secret_for_tenant(
            ref,
            value,
            store=secret_store,
            tenant_ctx=tenant_ctx,
        )


# Real upstream API endpoints for built-in connectors that aren't in the curated
# CONNECTOR_CATALOG, so the UI can show where a "builtin://" connector actually
# calls (the endpoint is baked into the built-in server implementation).
_BUILTIN_UPSTREAM_URLS: dict[str, str] = {
    "tavily": "https://api.tavily.com",
    "telegram": "https://api.telegram.org",
    "todoist": "https://api.todoist.com/rest/v2",
    "discord": "https://discord.com/api",
    "brave search": "https://api.search.brave.com",
    "serpapi": "https://serpapi.com/search",
    "firecrawl": "https://api.firecrawl.dev",
    "airtable": "https://api.airtable.com/v0",
    "gmail": "https://gmail.googleapis.com",
    "google sheets": "https://sheets.googleapis.com/v4",
    "google calendar": "https://www.googleapis.com/calendar/v3",
}

_CATALOG_URL_CACHE: dict[str, str] | None = None


def _upstream_url_for(name: str) -> str:
    """Best-effort real upstream API URL for a connector by name (catalog first,
    then a small supplement for common built-ins). Empty when unknown/local."""
    global _CATALOG_URL_CACHE
    if _CATALOG_URL_CACHE is None:
        try:
            from app.mcp.catalog import CONNECTOR_CATALOG

            _CATALOG_URL_CACHE = {
                s.name.lower(): s.default_url for s in CONNECTOR_CATALOG if s.default_url
            }
        except Exception:
            _CATALOG_URL_CACHE = {}
    key = (name or "").strip().lower()
    return _CATALOG_URL_CACHE.get(key) or _BUILTIN_UPSTREAM_URLS.get(key, "")


def _public_connector(server_id: str, cfg: MCPServerConfig) -> dict[str, Any]:
    data = cfg.model_dump(exclude={"server_id"})
    data["auth_config"] = _mask_auth_config(dict(cfg.auth_config))
    # A row written before MDB-01 may still hold a connection string in clear.
    for field in ("url", "base_url", "ws_url"):
        if is_dsn(data.get(field)):
            data["display_url"] = data.get("display_url") or mask_dsn(str(data[field]))
            data[field] = "builtin://" if cfg.builtin_type else mask_dsn(str(data[field]))
    if not data.get("display_url"):
        for value in cfg.auth_config.values():
            if is_dsn(value):
                data["display_url"] = mask_dsn(str(value))
                break
    # Expose whether this connector has a native builtin Python handler
    # so the frontend can show the ⚡ Built-in badge on registered connectors.
    from app.mcp.registry import MCPRegistry as _MCPReg

    builtin_type = cfg.builtin_type
    builtin_cfg = _builtin_config_for_type(builtin_type) if builtin_type else None
    data["has_builtin"] = (
        cfg.builtin_handler is not None
        or builtin_cfg is not None
        or _MCPReg.get_builtin_handler(server_id) is not None
    )
    # Display name (unique per tenant) and the built-in type it dispatches to, so
    # the UI can list several connections of one type by name (server_id is opaque).
    data["display_name"] = cfg.name
    data["builtin_type"] = builtin_type
    data["builtin_type_name"] = str(builtin_cfg.get("name", "")) if builtin_cfg else ""
    # The catalog type key the UI groups instances by ("mongodb", "google_sheets").
    data["connector_type"] = _connector_type_for(builtin_type) if builtin_type else ""
    # The stored url is "builtin://" for built-in connectors (a dispatch marker);
    # surface the real upstream API endpoint separately so the UI can show it.
    if data.get("display_url"):
        # A8: the configured host (masked: no credentials), not a catalog default.
        data["upstream_url"] = data["display_url"]
    elif builtin_type == _MONGODB_BUILTIN:
        data["upstream_url"] = ""  # nothing configured to show (never localhost)
    elif (cfg.url or "").startswith("builtin://"):
        data["upstream_url"] = _upstream_url_for(data["builtin_type_name"] or cfg.name)
    return {"server_id": server_id, **data}


def _preserve_redacted_auth_config(
    incoming: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(incoming)
    for key, value in incoming.items():
        if value == _REDACTED and key in existing:
            merged[key] = existing[key]
    return merged


def _mcp_initialize_payload() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "agentverse-test",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agentverse", "version": "0.1.0"},
        },
    }


@router.get("/catalog")
async def list_catalog(request: Request) -> list[dict]:
    """Return all connector types with auth field specs and per-tenant configured status."""
    tenant = _require_tenant(request)
    registry = _registry(request)

    # Determine which connectors this tenant already has registered
    configured_names: set[str] = set()
    try:
        servers = await registry.list_servers(tenant_ctx=tenant)
        for srv in servers:
            configured_names.add(srv.name.lower().strip())
    except Exception as exc:
        _logger.warning("list_catalog_registry_failed: %s", exc)

    result = []
    for spec in CONNECTOR_CATALOG:
        fields = [
            {
                "key": f.key,
                "label": f.label,
                "placeholder": f.placeholder,
                "field_type": f.field_type,
                "required": f.required,
                "hint": f.hint,
            }
            for f in spec.auth_fields
        ]
        result.append(
            {
                "name": spec.name,
                "display_name": spec.display_name or spec.name.replace("_", " ").title(),
                "description": spec.description,
                "auth_type": spec.auth_type,
                "default_url": spec.default_url,
                "icon": spec.icon,
                "category": spec.category,
                "auth_fields": fields,
                "has_builtin": bool(spec.builtin_server_id),
                "builtin_server_id": spec.builtin_server_id,
                "is_configured": spec.name.lower() in configured_names,
                "connector_type": spec.name,
            }
        )
    return result


_ENDPOINT_AUTH_KEYS = ("url", "base_url", "instance_url", "server_url", "endpoint")
# Database connection URIs (MongoDB / Postgres / MySQL / Redis built-ins).
_DSN_URL_SCHEMES = (
    "mongodb://",
    "mongodb+srv://",
    "postgres://",
    "postgresql://",
    "mysql://",
    "redis://",
    "rediss://",
)


async def _assert_connector_urls_public(
    url: str | None, auth_config: dict[str, Any] | None, *, context: str
) -> None:
    """400 unless every endpoint a connector will call is a public URL.

    Covers the connector URL AND endpoint-like auth_config keys (built-in
    handlers such as jira/github call auth_config['url'] as their API base).
    Registration checked only ``body.url``; update and OpenAPI import checked
    nothing, so a tenant could PUT a connector to http://169.254.169.254/.
    """
    from app.net.ssrf_guard import assert_public_url_async, private_access_networks

    candidates = [url or ""]
    for key in _ENDPOINT_AUTH_KEYS:
        value = (auth_config or {}).get(key)
        if isinstance(value, str) and value != _REDACTED and not is_connector_secret_ref(value):
            candidates.append(value)
    for raw in candidates:
        candidate = raw.strip()
        if not candidate or candidate.startswith("builtin://"):
            continue
        if candidate.lower().startswith(_DSN_URL_SCHEMES):
            # A database URI is not HTTP: check every host it dials (each
            # replica-set seed / SRV target) instead of refusing the scheme.
            from app.ingestion.connector_egress import check_source_dsn

            try:
                await check_source_dsn(candidate, context=context)
            except (SSRFError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Connector URL rejected by SSRF guard: {exc}",
                ) from exc
            continue
        if "://" not in candidate:
            candidate = f"https://{candidate}"  # same default as MCPClient
        try:
            await assert_public_url_async(
                candidate, context=context, allowed_networks=private_access_networks()
            )
        except (SSRFError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connector URL rejected by SSRF guard: {exc}",
            ) from exc


@router.get("")
async def list_connectors(request: Request) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    if hasattr(reg, "list_server_records"):
        records = await reg.list_server_records(tenant_ctx=tenant_ctx)
        return [_public_connector(server_id, cfg) for server_id, cfg in records]

    servers = await reg.list_servers(tenant_ctx=tenant_ctx)
    data = [s.model_dump() for s in servers]
    for item in data:
        item["auth_config"] = _mask_auth_config(dict(item.get("auth_config", {})))
    return data


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_connector(request: Request, body: RegisterConnectorRequest) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    pending_secrets: dict[str, str] = {}

    # Display names are unique per tenant: they are how a user, an agent's
    # connector picker and a workflow step tell connections apart.
    await _assert_unique_name(reg, body.name, tenant_ctx=tenant_ctx)

    # Built-in type: declared ("type"), else inferred from the name only when
    # that is unambiguous. Each connection gets its OWN id
    # (builtin-<type>:<slug>) — adopting the canonical id made a second
    # connection of the same type silently overwrite the first (config AND its
    # stored secrets). The handler is resolved by builtin_type, not by id.
    if body.builtin_type:
        _builtin_cfg = _builtin_config_for_type(body.builtin_type)
        if _builtin_cfg is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown connector type '{body.builtin_type}'",
            )
    else:
        # A3: a mongodb:// connection string IS the MongoDB built-in, whatever
        # the connection is called ("orders-db" used to become a remote MCP server).
        _inferred = _dsn_builtin_type(body.url, body.auth_config) or _infer_builtin_type(
            body.name, body.url
        )
        _builtin_cfg = _builtin_config_for_type(_inferred) if _inferred else None
    _canonical_id = str(_builtin_cfg.get("server_id")) if _builtin_cfg else ""
    _assert_builtin_enabled(_canonical_id)
    url = _effective_url(body.url, body.auth_config, _canonical_id)
    _assert_mongodb_policy(url, body.auth_config, _canonical_id)
    # SSRF guard: reject private/loopback/cloud-metadata URLs at registration time
    # (the connector URL and endpoint-like auth_config keys).
    await _assert_connector_urls_public(url, body.auth_config, context="connector registration")
    secret_store = _connector_secret_store(
        request,
        needs_secret_storage=_auth_config_requires_secret_storage(body.auth_config, url),
    )
    _builtin_tool_defs = list(_builtin_cfg.get("tool_definitions", [])) if _builtin_cfg else []
    _connection_id = (
        await _new_connection_id(reg, _canonical_id, body.name, tenant_ctx=tenant_ctx)
        if _canonical_id
        else ""
    )

    connection = {"id": _connection_id}

    def _new_suffix() -> None:
        if _canonical_id:
            connection["id"] = (
                f"{_canonical_id}:{_connection_slug(body.name)}-{uuid.uuid4().hex[:6]}"
            )

    built: dict[str, MCPServerConfig] = {}

    def _config_for(server_id: str) -> MCPServerConfig:
        sid = connection["id"] or server_id
        cfg = MCPServerConfig(
            server_id=sid,
            name=body.name,
            url=url,
            auth_type=body.auth_type,
            auth_config=_store_sensitive_auth_refs(
                sid,
                body.auth_config,
                pending_secrets,
            ),
            description=body.description,
            priority=body.priority,
            tool_definitions=_builtin_tool_defs,
            auto_approve=body.auto_approve,
            builtin_type=_canonical_id,
        )
        # MDB-01 / A7: one sealed copy of a connection string, never in clear.
        built["cfg"] = seal_connector_dsns(cfg, pending_secrets)
        return built["cfg"]

    server_id = await _create_connector(
        reg,
        _config_for,
        tenant_ctx=tenant_ctx,
        name=body.name,
        pending_secrets=pending_secrets,
        on_id_conflict=_new_suffix,
    )

    # The process-local handler registry is keyed by built-in TYPE.
    if _builtin_cfg is not None:
        MCPRegistry.register_builtin_handler(_canonical_id, _builtin_cfg["handler"])

    try:
        await _persist_connector_secrets(
            pending_secrets,
            secret_store=secret_store,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        await reg.unregister(server_id, tenant_ctx=tenant_ctx)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="connector secret storage failed",
        ) from exc
    stored = built.get("cfg")
    return {
        "server_id": server_id,
        "name": body.name,
        "display_name": body.name,
        "url": stored.url if stored is not None else url,
        "display_url": stored.display_url if stored is not None else "",
        "builtin_type": _canonical_id,
    }


@router.put("/{server_id}")
async def update_connector(
    request: Request, server_id: str, body: RegisterConnectorRequest
) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    existing = await reg.get(server_id, tenant_ctx=tenant_ctx)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )
    _assert_builtin_enabled(existing.builtin_type or _declared_type_on_update(body))
    if _name_key(body.name) != _name_key(existing.name):
        await _assert_unique_name(reg, body.name, tenant_ctx=tenant_ctx, exclude_id=server_id)
    auth_config = _preserve_redacted_auth_config(body.auth_config, dict(existing.auth_config))
    # The edit form sends back the masked display form (or the builtin marker) it
    # was shown: that is "unchanged", never a new connection string (MDB-01).
    url = body.url
    shown = {existing.display_url, mask_dsn(existing.url) if is_dsn(existing.url) else ""}
    if url and url in shown - {""}:
        url = existing.url
    url = _effective_url(
        url, auth_config, existing.builtin_type or _declared_type_on_update(body)
    )
    _assert_mongodb_policy(
        url, auth_config, existing.builtin_type or _declared_type_on_update(body)
    )
    # Update had no SSRF guard at all (only registration did).
    await _assert_connector_urls_public(url, auth_config, context="connector update")
    secret_store = _connector_secret_store(
        request,
        needs_secret_storage=_auth_config_requires_secret_storage(auth_config, url),
    )
    pending_secrets: dict[str, str] = {}
    stored_auth_config = _store_sensitive_auth_refs(server_id, auth_config, pending_secrets)
    cfg = seal_connector_dsns(
        MCPServerConfig(
            name=body.name,
            url=url,
            auth_type=body.auth_type,
            auth_config=stored_auth_config,
            description=body.description,
            priority=body.priority,
            auto_approve=body.auto_approve,
            # Preserve the canonical builtin id + tool defs on update so the connector
            # keeps its tools (a fresh UUID would strip them after a restart).
            server_id=existing.server_id,
            tool_definitions=list(existing.tool_definitions or []),
            # The connection's built-in type never changes on update (a legacy
            # connector without one may have it declared now).
            builtin_type=existing.builtin_type or _declared_type_on_update(body),
        ),
        pending_secrets,
        previous_display=existing.display_url,
    )
    try:
        await _persist_connector_secrets(
            pending_secrets,
            secret_store=secret_store,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="connector secret storage failed",
        ) from exc
    updated = await reg.update(server_id, cfg, tenant_ctx=tenant_ctx)
    _close_pooled_clients(tenant_ctx.tenant_id, server_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )
    return _public_connector(server_id, cfg)


_CONNECTOR_TEST_TOOLS: dict[str, tuple[str, dict]] = {
    "jira": (
        "jira_search_issues",
        {"jql": "created >= -7d ORDER BY created DESC", "max_results": 1},
    ),
    "github": ("github_list_repos", {"owner": "octocat", "per_page": 1}),
    "slack": ("slack_list_channels", {"limit": 1}),
    "linear": ("linear_list_issues", {"limit": 1}),
    "hubspot": ("hubspot_search_contacts", {"limit": 1}),
    "stripe": ("stripe_list_customers", {"limit": 1}),
    "gitlab": ("gitlab_list_projects", {"per_page": 1}),
    "confluence": ("confluence_list_spaces", {"limit": 1}),
    "sentry": ("sentry_list_issues", {"project_slug": "test", "limit": 1}),
    "mongodb": ("mongodb_list_collections", {}),
    "redis": ("redis_list_keys", {"pattern": "*"}),
    "postgresql": ("postgres_list_tables", {}),
}

# ---------------------------------------------------------------------------
# Direct REST API tests — bypass MCP tool infrastructure
# ---------------------------------------------------------------------------
# These functions make a direct REST API call using the connector's stored
# credentials to verify connectivity and token validity.  They are the primary
# test path; the mcp_client.call_tool path is only used as a fallback.
# ---------------------------------------------------------------------------


def _get_cred(cfg: Any, *keys: str, default: str = "") -> str:
    """Extract the first matching credential key from auth_config."""
    auth = cfg.auth_config or {}
    for k in keys:
        val = auth.get(k)
        if val and isinstance(val, str) and not val.startswith("secret://"):
            return val
    return default


async def _test_github(cfg: Any, started: float, server_id: str) -> dict[str, Any]:
    """Test GitHub connectivity by validating the PAT against the GitHub REST API.

    The same Personal Access Token (PAT) is used for both:
      - GitHub REST API  (https://api.github.com)
      - GitHub MCP Server (https://api.githubcopilot.com/mcp/)

    We always validate against the REST /user endpoint because:
      - It gives a clear "authenticated as @username" confirmation
      - It returns actionable 401/403 error messages
      - The MCP endpoint does not have a simple unauthenticated GET for ping

    If the connector URL is an Enterprise GHE instance we still validate the PAT
    against that instance's /api/v3/user endpoint.
    """
    token = _get_cred(cfg, "token", "api_token", "access_token", "password")
    configured_url = (cfg.url or cfg.base_url or "").rstrip("/")

    # Determine the REST API base to use for PAT validation:
    # - Official MCP endpoint  → validate against https://api.github.com
    # - github.com REST API    → already correct
    # - GitHub Enterprise URL  → use <ghes>/api/v3
    if (
        not configured_url
        or "githubcopilot.com" in configured_url
        or configured_url == "https://api.github.com"
    ):
        rest_base = "https://api.github.com"
    elif configured_url.endswith("/api/v3") or configured_url.endswith("/api/v3/"):
        rest_base = configured_url.rstrip("/")
    else:
        # GitHub Enterprise: the MCP path is typically <host>/api/mcp;
        # fall back to <host>/api/v3 for REST validation
        rest_base = configured_url.rstrip("/") + "/api/v3"

    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with _probe_client() as client:
            resp = await client.get(f"{rest_base}/user", headers=headers)
        latency_ms = round((time.time() - started) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            login = data.get("login", "?")
            name = data.get("name") or ""
            scopes = resp.headers.get("X-OAuth-Scopes", "")
            detail = f"Authenticated as @{login}"
            if name:
                detail += f" ({name})"
            if scopes:
                detail += f" · scopes: {scopes}"
            return {
                "server_id": server_id,
                "reachable": True,
                "status": "passed",
                "latency_ms": latency_ms,
                "detail": detail,
                "mcp_url": "https://api.githubcopilot.com/mcp/",
            }

        if resp.status_code == 401:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": (
                    "Invalid token — GitHub returned 401 Unauthorized.\n"
                    "Check your Personal Access Token at github.com/settings/tokens."
                ),
                "latency_ms": latency_ms,
            }

        if resp.status_code == 403:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": (
                    "Token lacks required scopes — GitHub returned 403 Forbidden.\n"
                    "Add the 'repo' and 'read:org' scopes at github.com/settings/tokens."
                ),
                "latency_ms": latency_ms,
            }

        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": f"GitHub API returned HTTP {resp.status_code}",
            "latency_ms": latency_ms,
        }

    except httpx.ConnectError:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": "Cannot reach api.github.com — check your network connection.",
            "latency_ms": round((time.time() - started) * 1000),
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }


def _probe_client() -> httpx.AsyncClient:
    """Client for connector test probes, pinned to the IPs checked at connect.

    ``test_connector`` SSRF-checks the tenant URL first; a plain
    ``httpx.AsyncClient`` resolved the name again, so a DNS answer flipping to
    127.0.0.1 / 169.254.169.254 (rebinding) was probed with the tenant's token.
    """
    from app.net.ssrf_guard import private_access_networks

    client: httpx.AsyncClient = public_async_client(
        timeout=10.0, allowed_networks=private_access_networks()
    )
    return client


async def _test_jira(cfg: Any, started: float, server_id: str) -> dict[str, Any]:
    token = _get_cred(cfg, "api_token", "token", "password")
    email = _get_cred(cfg, "email", "username", "user")
    base = (cfg.url or cfg.base_url or "").rstrip("/")
    if not base:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": "Jira base URL not configured.",
            "latency_ms": 0,
        }
    try:
        headers: dict[str, str] = {"Accept": "application/json"}
        if email and token:
            import base64 as _b64

            cred = _b64.b64encode(f"{email}:{token}".encode()).decode()
            headers["Authorization"] = f"Basic {cred}"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        async with _probe_client() as client:
            resp = await client.get(f"{base}/rest/api/3/myself", headers=headers)
        latency_ms = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "server_id": server_id,
                "reachable": True,
                "status": "passed",
                "latency_ms": latency_ms,
                "detail": f"Authenticated as {data.get('displayName', data.get('emailAddress', '?'))}",  # noqa: E501
            }
        if resp.status_code == 401:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": "Invalid credentials — check email and API token.",
                "latency_ms": latency_ms,
            }
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": f"Jira returned HTTP {resp.status_code}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }


async def _test_slack(cfg: Any, started: float, server_id: str) -> dict[str, Any]:
    token = _get_cred(cfg, "token", "bot_token", "api_token", "access_token")
    try:
        async with _probe_client() as client:
            resp = await client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        latency_ms = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return {
                    "server_id": server_id,
                    "reachable": True,
                    "status": "passed",
                    "latency_ms": latency_ms,
                    "detail": f"Connected as {data.get('user', '?')} in {data.get('team', '?')}",
                }
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": data.get("error", "auth.test returned ok=false"),
                "latency_ms": latency_ms,
            }
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": f"Slack returned HTTP {resp.status_code}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }


async def _test_stripe(cfg: Any, started: float, server_id: str) -> dict[str, Any]:
    token = _get_cred(cfg, "api_key", "secret_key", "token", "api_token")
    try:
        async with _probe_client() as client:
            resp = await client.get(
                "https://api.stripe.com/v1/account",
                headers={"Authorization": f"Bearer {token}"},
            )
        latency_ms = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "server_id": server_id,
                "reachable": True,
                "status": "passed",
                "latency_ms": latency_ms,
                "detail": f"Account: {data.get('id', '?')}",
            }
        if resp.status_code == 401:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": "Invalid Stripe API key.",
                "latency_ms": latency_ms,
            }
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": f"Stripe returned HTTP {resp.status_code}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }


async def _test_gitlab(cfg: Any, started: float, server_id: str) -> dict[str, Any]:
    token = _get_cred(cfg, "token", "private_token", "api_token", "access_token")
    base = (cfg.url or cfg.base_url or "https://gitlab.com").rstrip("/")
    try:
        async with _probe_client() as client:
            resp = await client.get(
                f"{base}/api/v4/user",
                headers={"PRIVATE-TOKEN": token} if token else {},
            )
        latency_ms = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "server_id": server_id,
                "reachable": True,
                "status": "passed",
                "latency_ms": latency_ms,
                "detail": f"Authenticated as @{data.get('username', '?')}",
            }
        if resp.status_code == 401:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": "Invalid GitLab token.",
                "latency_ms": latency_ms,
            }
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": f"GitLab returned HTTP {resp.status_code}",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }


# Map connector name → direct REST test function
_DIRECT_REST_TESTS: dict[str, Any] = {
    "github": _test_github,
    "jira": _test_jira,
    "slack": _test_slack,
    "stripe": _test_stripe,
    "gitlab": _test_gitlab,
}


@router.post("/{server_id}/test")
async def test_connector(request: Request, server_id: str) -> dict[str, Any]:
    """Test a connector by validating credentials against its REST API.

    Test priority:
    1. Direct REST API call (github → GET /user, jira → GET /myself, etc.)
       bypasses MCP tool infrastructure; works even before builtin_handler is
       loaded into the process.
    2. mcp_client.call_tool — used only for connectors without a direct test.
    3. Generic HTTP HEAD/GET to the configured URL.
    """
    tenant = _require_tenant(request)
    registry = _registry(request)
    started = time.time()

    cfg = await registry.get(server_id, tenant_ctx=tenant)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    # NF-13: a built-in type the operator switched off is never contacted.
    from app.mcp.builtin_kill_switch import DISABLED_STATUS, disabled_reason

    _disabled = disabled_reason(cfg.builtin_type or "")
    if _disabled is not None:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "reason": DISABLED_STATUS,
            "error": _disabled,
            "latency_ms": round((time.time() - started) * 1000),
        }

    # ── Resolve vault secret references in auth_config ─────────────────────
    # Tokens are stored as "secret://connector/<id>/token" vault refs.
    # Direct test functions call _get_cred() which skips vault refs,
    # so we must resolve them here before dispatching.
    resolved_auth_config: dict[str, Any] = {}
    secret_store = _connector_secret_store(request)
    for key, value in (cfg.auth_config or {}).items():
        if isinstance(value, str) and is_connector_secret_ref(value):
            plain: str | None = None
            undecryptable = ""
            try:
                plain = await resolve_connector_secret_ref_for_tenant(
                    value, store=secret_store, tenant_ctx=tenant
                )
            except ConnectorSecretUndecryptableError as exc:
                # Stored, but this process cannot open it (vault key mismatch):
                # "re-enter it" would not help — say why instead.
                undecryptable = str(exc)
                plain = None
            except Exception:
                plain = None
            if not plain:
                # Used to fall back to sending the raw "vault://…" reference as
                # the credential (and then report the vendor's 401 as the result).
                return {
                    "server_id": server_id,
                    "reachable": False,
                    "status": "failed",
                    "error": (
                        f"Stored credential '{key}' could not be resolved: it is stored but "
                        f"cannot be decrypted here: {undecryptable}"
                        if undecryptable
                        else f"Stored credential '{key}' could not be resolved; re-enter it."
                    ),
                    "latency_ms": round((time.time() - started) * 1000),
                }
            resolved_auth_config[key] = plain
        else:
            resolved_auth_config[key] = value
    # Overlay resolved values onto a copy of cfg so test functions see plain text
    cfg = cfg.model_copy(update={"auth_config": resolved_auth_config})

    # Test by built-in TYPE ("orders-db" is a MongoDB connection), not display name.
    _type_cfg = _builtin_config_for_type(cfg.builtin_type) if cfg.builtin_type else None
    connector_name = str(_type_cfg.get("name", "") if _type_cfg else cfg.name).lower().strip()

    # Test-time SSRF guard. Registration/update check the URL, but rows written
    # before those checks existed, or a DNS name re-pointed since (rebinding),
    # reached the direct GitHub/Jira/GitLab probes and the generic GET unchecked.
    for _url in {cfg.url or "", cfg.base_url or ""}:
        if _url.startswith(("http://", "https://")):
            try:
                await assert_public_url_async(
                    _url, context="connector test", allowed_networks=private_access_networks()
                )
            except SSRFError as ssrf_exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="SSRF protection: disallowed URL",
                ) from ssrf_exc

    # ── 1. Direct REST test (primary path) ────────────────────────────────────
    direct_test_fn = _DIRECT_REST_TESTS.get(connector_name)
    if direct_test_fn:
        return await direct_test_fn(cfg, started, server_id)

    # ── 2. mcp_client.call_tool (for connectors with an MCP tool entry) ───────
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        from app.mcp.client import MCPClient

        # Tenant-scoped secret resolution: sealed credentials (connection
        # strings, tokens) are resolved from this app's connector secret store.
        mcp_client = MCPClient(registry=registry, secret_resolver=_secret_resolver(request))

    test_entry = _CONNECTOR_TEST_TOOLS.get(connector_name)
    if test_entry:
        tool_name, tool_args = test_entry
        try:
            result = await mcp_client.call_tool(
                server_id=server_id,
                tool_name=tool_name,
                arguments=tool_args,
                tenant_ctx=tenant,
            )
            latency_ms = round((time.time() - started) * 1000)
            if result.success and getattr(result, "stale", False) is True:
                # a02-F030-04: a cached result served because the circuit is
                # open proves nothing about the connector now.
                return {
                    "server_id": server_id,
                    "reachable": False,
                    "status": "failed",
                    "error": "Connector unavailable (circuit open); only a cached result exists.",
                    "latency_ms": latency_ms,
                }
            if result.success:
                return {
                    "server_id": server_id,
                    "reachable": True,
                    "latency_ms": latency_ms,
                    "status": "passed",
                }
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": result.error or "Tool call failed",
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            return {
                "server_id": server_id,
                "reachable": False,
                "status": "failed",
                "error": str(exc),
                "latency_ms": round((time.time() - started) * 1000),
            }

    # ── 3. Generic reachability check ─────────────────────────────────────────
    url = cfg.url or cfg.base_url
    if not url or url.startswith("builtin://"):
        # Nothing was contacted and no credential was checked: say so instead
        # of reporting the connector reachable.
        return {
            "server_id": server_id,
            "reachable": None,
            "status": "not_tested",
            "detail": "No test is available for this connector; nothing was contacted.",
            "latency_ms": 0,
        }

    if "://" not in url:
        url = f"https://{url}"  # same default as MCPClient
    # SSRF protection: validate the URL before making any outbound request.
    # Never allow requests to internal/metadata endpoints. Async (never the
    # blocking getaddrinfo on the event loop); the pinned probe client
    # re-validates the IPs it actually connects to. Outside the probe's
    # try/except so a refusal is a 400, not a "failed" test.
    try:
        await assert_public_url_async(
            url, context="connector test", allowed_networks=private_access_networks()
        )
    except (SSRFError, ValueError) as ssrf_exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSRF protection: disallowed URL",
        ) from ssrf_exc
    try:
        # Build auth headers from connector config — never forward the incoming
        # request's own Authorization header to external services.
        headers: dict[str, str] = {}
        for key, value in (cfg.auth_config or {}).items():
            if isinstance(value, str) and (
                "token" in key.lower() or "authorization" in key.lower()
            ):
                headers["Authorization"] = f"Bearer {value}"
                break
        async with _probe_client() as hclient:
            resp = await hclient.get(url, headers=headers)
        latency_ms = round((time.time() - started) * 1000)
        reachable = resp.status_code < 500
        # 401/403 (and any other 4xx) used to count as "passed" — the credential
        # the tenant just entered could be rejected and the test still went green.
        passed = resp.status_code < 400
        out: dict[str, Any] = {
            "server_id": server_id,
            "reachable": reachable,
            "status": "passed" if passed else "failed",
            "latency_ms": latency_ms,
            "http_status": resp.status_code,
        }
        if resp.status_code in (401, 403):
            out["error"] = "Credentials were rejected by the connector endpoint"
        elif not passed:
            out["error"] = f"Connector endpoint returned HTTP {resp.status_code}"
        return out
    except Exception as exc:
        return {
            "server_id": server_id,
            "reachable": False,
            "status": "failed",
            "error": str(exc),
            "latency_ms": round((time.time() - started) * 1000),
        }


@router.get("/{server_id}/health")
async def get_connector_health_history(
    request: Request, server_id: str, limit: int = Query(20, ge=1, le=200)
) -> list[dict[str, Any]]:
    """Return health check history for a connector.

    No database or a failed read is a 503 — never ``[]``, which the UI shows as
    "never checked" (MCPREG-02). Another tenant's (or an unknown) connector is a
    404, as on every other connector route (P1c-5).
    """
    tenant = _require_tenant(request)
    if await _registry(request).get(server_id, tenant_ctx=tenant) is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector health history is unavailable (no database configured).",
        )
    try:
        from sqlalchemy import select

        from app.db.models.mcp import ConnectorHealthSnapshot
        from app.db.rls import sqlalchemy_rls_context

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant.tenant_id),
        ):
            result = await session.execute(
                select(ConnectorHealthSnapshot)
                .where(
                    ConnectorHealthSnapshot.server_id == server_id,
                    ConnectorHealthSnapshot.tenant_id == tenant.tenant_id,
                )
                .order_by(ConnectorHealthSnapshot.checked_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
        return [
            {
                "status": r.status,
                "latency_ms": r.latency_ms,
                "error": r.error,
                "checked_at": r.checked_at.isoformat() if r.checked_at else "",
            }
            for r in rows
        ]
    except Exception as exc:
        _logger.error(
            "connector_health_history_read_failed tenant=%s server=%s error=%s",
            tenant.tenant_id,
            server_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector health history is temporarily unavailable; retry shortly.",
        ) from exc


# The connector_name "popup" OAuth flow (POST /connectors/oauth/start and POST
# /connectors/oauth/callback) was removed (OAUTH-06): its CSRF state lived in a
# process-local dict and its completion could only answer 501. OAuth connectors
# connect through the PKCE flow below (registered connector, Redis-shared state).


def _default_redirect_uri(request: Request) -> str:
    """Derive the OAuth callback redirect URI from settings or the request base URL."""
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        frontend_url = getattr(settings, "frontend_url", "") or ""
        if frontend_url:
            return f"{frontend_url.rstrip('/')}/connectors/oauth/callback"
    # Fallback to request base URL
    base = str(request.base_url).rstrip("/")
    return f"{base}/connectors/oauth/callback"


@router.get("/oauth/start")
async def oauth_start(request: Request, server_id: str) -> dict[str, Any]:
    """Start OAuth PKCE flow — returns the authorization URL with PKCE challenge."""
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)

    cfg = await reg.get(server_id, tenant_ctx=tenant_ctx)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )

    if cfg.auth_type not in {"oauth_ac", "pkce", "oauth_cc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connector {cfg.name} uses auth_type={cfg.auth_type}, not OAuth",
        )

    # Use the real OAuthFlowManager
    oauth_manager = getattr(request.app.state, "oauth_manager", None)
    if oauth_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth manager not configured",
        )

    # Build the full authorization URL
    authorize_url = cfg.auth_config.get("authorize_url", "")
    client_id = cfg.auth_config.get("client_id", "")
    # A redirect_uri registered with the provider (auth_config) wins; otherwise
    # derive one. Either way it is stored with the PKCE state below so the token
    # exchange in /oauth/callback repeats EXACTLY this value. It used to re-derive
    # a different one there (frontend_url, no ?server_id=), so every real provider
    # rejected the code exchange with invalid_grant.
    # Default: the frontend's callback route. The provider redirects the
    # browser there, and the frontend completes the exchange by calling
    # GET /connectors/oauth/callback with the user's credentials. (The default
    # used to be this backend route itself, which a browser redirect cannot
    # authenticate against, so no flow could finish.)
    redirect_uri = str(cfg.auth_config.get("redirect_uri") or "") or (
        _default_redirect_uri(request) + f"?server_id={server_id}"
    )

    # Start the PKCE flow — generates state token + code challenge (shared
    # across replicas when Redis is wired).
    pkce_params = await oauth_manager.astart_flow(
        server_id=server_id, tenant_ctx=tenant_ctx, redirect_uri=redirect_uri
    )

    if authorize_url and client_id:
        from urllib.parse import urlencode

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": pkce_params["state"],
            "code_challenge": pkce_params["code_challenge"],
            "code_challenge_method": "S256",
        }
        full_auth_url = f"{authorize_url}?{urlencode(params)}"
    else:
        full_auth_url = (
            f"Configure authorize_url and client_id in auth_config for connector {server_id}"
        )

    return {
        "server_id": server_id,
        "auth_url": full_auth_url,
        "state": pkce_params["state"],
        "redirect_uri": redirect_uri,
        "instructions": "Redirect the user to auth_url to begin authorization.",
    }


# Keys the OAuth callback used to copy tokens into (SECRET-03; never read).
_LEGACY_OAUTH_COPY_KEYS = frozenset(
    {"_encrypted_access_token", "_encrypted_refresh_token", "_token_scope", "_token_type"}
)


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    server_id: str = "",
    redirect_uri: str = "",  # Legacy fallback only — see below
) -> dict[str, Any]:
    """OAuth callback — exchange authorization code for access tokens via PKCE.

    The token request reuses the redirect_uri stored with the PKCE state by
    /oauth/start; ``redirect_uri`` here is only used for a flow started without
    one (OAuthFlowManager.exchange_code prefers the stored value).
    """
    if not redirect_uri:
        redirect_uri = _default_redirect_uri(request)
    tenant_ctx = _require_tenant(request)

    # Get the OAuth manager from app state
    oauth_manager = getattr(request.app.state, "oauth_manager", None)
    if oauth_manager is None:
        # OAuthFlowManager not configured — return a clear error instead of faking success.
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth flow manager is not configured. "
                "Ensure OAuthFlowManager is wired in the application factory."
            ),
        )

    # Look up the server config to get token URL and client_id
    reg = _registry(request)
    cfg = await reg.get(server_id, tenant_ctx=tenant_ctx) if server_id else None

    token_url = ""
    client_id = ""
    if cfg is not None:
        token_url = cfg.auth_config.get("token_url", "")
        client_id = cfg.auth_config.get("client_id", "")

    if not token_url or not code or not state:
        # Cannot exchange without a token URL — return pending_config status
        return {
            "server_id": server_id,
            "status": "pending_config",
            "message": (
                "Token URL not configured for this connector. "
                "Set auth_config.token_url and auth_config.client_id."
            ),
            "received_code": bool(code),
            "received_state": bool(state),
        }

    # Exchange the authorization code; each failure has its own status code
    # and a safe message (OAUTH-05) — never 200 {status: error} with raw
    # exception text, and never "invalid state" for an unrelated error.
    exchange = (
        oauth_manager.exchange_code_or_raise
        if isinstance(oauth_manager, OAuthFlowManager)
        else oauth_manager.exchange_code
    )
    try:
        token = await exchange(
            code=code,
            state=state,
            token_url=token_url,
            client_id=client_id,
            redirect_uri=redirect_uri,
            tenant_ctx=tenant_ctx,
        )
    except OAuthTokenPersistError as exc:
        # The provider issued a token but it could not be stored durably: the
        # worker and other replicas would never see it — not "connected".
        _logger.error(
            "oauth_callback_persist_failed tenant=%s server=%s error=%s",
            tenant_ctx.tenant_id,
            server_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The connection could not be saved; nothing was stored. "
                "Retry connecting the connector."
            ),
        ) from exc
    except OAuthExchangeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.public_message, "server_id": server_id},
        ) from exc
    except Exception as exc:
        _logger.error(
            "oauth_callback_exchange_failed tenant=%s server=%s error=%s",
            tenant_ctx.tenant_id,
            server_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "oauth_exchange_failed",
                "message": "The OAuth token exchange failed.",
                "server_id": server_id,
            },
        ) from exc

    if token is None:
        err = OAuthInvalidStateError()
        raise HTTPException(
            status_code=err.status_code,
            detail={"code": err.code, "message": err.public_message, "server_id": server_id},
        )

    # The token lives only in oauth_tokens (written by exchange_code, sealed with
    # the tenant's envelope key). The callback used to also seal a copy into the
    # connector's auth_config, where nothing read it (SECRET-03); a copy left by
    # an earlier connect is removed here.
    if cfg is not None:
        stale = [k for k in cfg.auth_config if k in _LEGACY_OAUTH_COPY_KEYS]
        if stale:
            cleaned = {k: v for k, v in cfg.auth_config.items() if k not in stale}
            updated_cfg = cfg.model_copy(update={"auth_config": cleaned})
            if not await reg.update(server_id, updated_cfg, tenant_ctx=tenant_ctx):
                raise HTTPException(status_code=404, detail="Connector not found")

    return {
        "server_id": server_id,
        "status": "connected",
        "message": "OAuth tokens stored securely in credential vault.",
        "token_type": token.token_type,
        "scope": token.scope,
        "has_refresh_token": bool(token.refresh_token),
    }


@router.get("/{connector_id}/usage")
async def get_connector_usage(
    connector_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Return the goals that used exactly this connector (MCPREG-03).

    Reads ``goal_connector_usage`` (written when a goal's tool call reaches the
    connector) with an exact, indexed match — never a substring, so
    ``builtin-github`` does not count ``builtin-github:work-org``. No database or
    a failed read is a 503, never zeros.
    """
    from app.mcp.connector_usage import connector_usage

    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector usage is unavailable (no database configured).",
        )
    try:
        usage = await connector_usage(db, tenant.tenant_id, connector_id, limit=limit)
    except Exception as exc:
        _logger.error(
            "connector_usage_read_failed tenant=%s connector=%s error=%s",
            tenant.tenant_id,
            connector_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector usage is temporarily unavailable; retry shortly.",
        ) from exc
    total = usage["total"]
    success_rate = round(usage["success_count"] / total * 100, 1) if total > 0 else None
    return {
        "goals": usage["goals"],
        "total": total,
        "success_rate": success_rate,
        "connector_id": connector_id,
        "filtered": True,
    }


async def _erase_connector_secrets(store: Any, server_id: str, tenant_ctx: Any) -> None:
    """Delete every stored secret of one connector (raises when it cannot)."""
    if store is None:
        return
    deleter = getattr(store, "delete_server", None)
    if deleter is not None:
        result = deleter(server_id, tenant_ctx=tenant_ctx)
        if inspect.isawaitable(result):
            await result
        return
    if isinstance(store, dict):  # dev / test in-process store keyed by ref
        prefix = connector_secret_ref(server_id, "")
        for ref in [r for r in store if str(r).startswith(prefix)]:
            store.pop(ref, None)


async def _purge_connector_rows(db: Any, tenant_id: str, server_id: str) -> None:
    """Drop the connector's OAuth token row and tool capability rows (raises)."""
    if db is None:
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        await session.execute(
            text("DELETE FROM oauth_tokens WHERE tenant_id = :t AND server_id = :sid"),
            {"t": tenant_id, "sid": server_id},
        )
        await session.execute(
            text("DELETE FROM tool_capabilities WHERE tenant_id = :t AND connector_id = :sid"),
            {"t": tenant_id, "sid": server_id},
        )


_MONGODB_SCHEMES = ("mongodb://", "mongodb+srv://")
_MONGODB_BUILTIN = "builtin-mongodb"
_URI_AUTH_KEYS = ("uri", "connection_string", "url", "base_url", "mongodb_uri", "dsn")


def _is_mongodb_uri(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(_MONGODB_SCHEMES)


def _dsn_builtin_type(url: str, auth_config: dict[str, Any]) -> str | None:
    """'builtin-mongodb' when the connection is a MongoDB connection string (A3)."""
    if _is_mongodb_uri(url) or any(_is_mongodb_uri(v) for v in auth_config.values()):
        return _MONGODB_BUILTIN
    return None


def _effective_url(url: str, auth_config: dict[str, Any], builtin_type: str) -> str:
    """A7: for a MongoDB connection the URI in auth_config is the ONE source of truth.

    The catalog form also sends its ``default_url`` (``mongodb://localhost:27017``)
    as the top-level url; that copy is never used, so it is neither stored nor
    egress-checked. A sealed URI (``<redacted>`` / a vault reference) counts too.
    """
    if builtin_type != _MONGODB_BUILTIN:
        return url
    has_uri = any(
        _is_mongodb_uri(auth_config.get(k))
        or is_connector_secret_ref(auth_config.get(k))
        or auth_config.get(k) == _REDACTED
        for k in _URI_AUTH_KEYS
    )
    return "builtin://" if has_uri else url


def _assert_mongodb_policy(url: str, auth_config: dict[str, Any], builtin_type: str) -> None:
    """422 at SAVE time for a MongoDB connection the call-time policy refuses (NF-2).

    The same shared policy the MCP handler and the ingestion connector apply:
    URI options read like the driver (no file paths / proxies / ambient-identity
    mechanisms) and nothing that weakens TLS — on every connection string and on
    the connector's own fields (also when the URI itself is kept sealed).
    """
    dsns = [
        v.strip()
        for v in (url, *auth_config.values())
        if isinstance(v, str) and v.strip().lower().startswith(_MONGODB_SCHEMES)
    ]
    if not dsns and builtin_type != "builtin-mongodb":
        return
    from app.net.mongodb_policy import assert_mongo_connection_allowed

    try:
        for dsn in dsns or [""]:
            assert_mongo_connection_allowed(dsn, auth_config)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def _close_pooled_clients(tenant_id: str, server_id: str) -> None:
    """Close the pooled driver clients of a connector that changed or went away.

    This replica's clients close now; other replicas never reuse them for new
    credentials (the credential fingerprint is part of the pool key) and close
    them by idle TTL.
    """
    from app.mcp import mongodb_clients

    mongodb_clients.evict(tenant_id, server_id)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_connector(request: Request, server_id: str) -> None:
    """Remove a connector AND everything it holds (MCPREG-05).

    Credentials are erased first: if that fails the connector is kept (503) so
    the delete can be retried — never a removed connector whose encrypted
    secrets, OAuth token or tool rows linger forever.
    """
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    if await reg.get(server_id, tenant_ctx=tenant_ctx) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )
    store = getattr(request.app.state, "connector_secret_store", None)
    db = getattr(request.app.state, "db_session_factory", None)
    try:
        await _erase_connector_secrets(store, server_id, tenant_ctx)
        await _purge_connector_rows(db, tenant_ctx.tenant_id, server_id)
    except Exception as exc:
        _logger.error(
            "connector_erasure_failed tenant=%s server=%s error=%s",
            tenant_ctx.tenant_id,
            server_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector credentials could not be erased; the connector was kept. Retry.",
        ) from exc
    oauth = getattr(request.app.state, "oauth_manager", None)
    drop = getattr(oauth, "_drop_cached", None)
    if drop is not None:
        drop((tenant_ctx.tenant_id, server_id))
    removed = await reg.unregister(server_id, tenant_ctx=tenant_ctx)
    _close_pooled_clients(tenant_ctx.tenant_id, server_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )


# ── OpenAPI auto-import ───────────────────────────────────────────────────────


class OpenAPIImportRequest(BaseModel):
    openapi_spec: str
    base_url: str
    name: str = ""
    # Deliberately ``str``: an unknown value is normalised to bearer below.
    auth_type: str = "bearer"
    auth_config: dict[str, Any] = {}
    description: str = ""


@router.post("/import-openapi", status_code=status.HTTP_201_CREATED)
async def import_openapi_connector(request: Request, body: OpenAPIImportRequest) -> dict[str, Any]:
    """Import an OpenAPI 3.x spec and register it as a connector with extracted tools."""
    tenant_ctx = _require_tenant(request)
    # OpenAPI import registered base_url with no SSRF guard.
    await _assert_connector_urls_public(
        body.base_url, body.auth_config, context="connector OpenAPI import"
    )

    from app.mcp.openapi_importer import extract_tools_from_spec, parse_openapi_spec

    try:
        spec = parse_openapi_spec(body.openapi_spec)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot parse OpenAPI spec: {exc}",
        ) from exc

    import uuid

    placeholder_id = uuid.uuid4().hex
    tools = extract_tools_from_spec(
        spec, connector_id=placeholder_id, tenant_id=tenant_ctx.tenant_id
    )

    # Normalise auth_type to a known Literal (default to bearer for unknown types)
    _valid_auth_types = {
        "bearer",
        "api_key",
        "oauth_ac",
        "oauth_cc",
        "pkce",
        "basic",
        "custom_header",
        "mtls",
        "hmac",
    }
    from app.mcp.openapi_importer import resolve_auth_from_spec, tool_definitions_from_extracted

    if not tools:
        raise HTTPException(
            status_code=422,
            detail="OpenAPI spec contains no importable operations",
        )

    # An explicit auth_type wins; otherwise the spec's securitySchemes decide the
    # type and placement (header / query / cookie) for the supplied credential.
    auth_config: dict[str, Any] = dict(body.auth_config)
    if "auth_type" in body.model_fields_set:
        safe_auth_type: Any = body.auth_type if body.auth_type in _valid_auth_types else "bearer"
    else:
        spec_auth_type, auth_config = resolve_auth_from_spec(spec, auth_config)
        safe_auth_type = spec_auth_type

    connector_name = body.name or (spec.get("info", {}).get("title") or "Imported API")
    connector_desc = body.description or f"Auto-imported from OpenAPI spec ({len(tools)} endpoints)"
    tool_defs = tool_definitions_from_extracted(tools)

    # Credentials go to the tenant-scoped encrypted secret store and only a
    # ``vault://connectors/<id>/<key>`` reference is kept on the connector —
    # this endpoint used to persist ``auth_config`` (API keys, tokens,
    # passwords) in plaintext in the registry.
    secret_store = _connector_secret_store(
        request,
        needs_secret_storage=_auth_config_requires_secret_storage(auth_config),
    )
    pending_secrets: dict[str, str] = {}

    def _config_for(server_id: str) -> MCPServerConfig:
        return MCPServerConfig(
            server_id=server_id,
            name=connector_name,
            url=body.base_url,
            auth_type=safe_auth_type,
            auth_config=_store_sensitive_auth_refs(server_id, auth_config, pending_secrets),
            description=connector_desc,
            # Without these the imported operations were invisible to the
            # planner and undispatchable (the config carried no tools at all).
            tool_definitions=tool_defs,
            capabilities=sorted({str(t["name"]) for t in tool_defs}),
        )

    reg = _registry(request)
    server_id = await _create_connector(
        reg,
        _config_for,
        tenant_ctx=tenant_ctx,
        name=connector_name,
        pending_secrets=pending_secrets,
    )
    try:
        await _persist_connector_secrets(
            pending_secrets,
            secret_store=secret_store,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        await reg.unregister(server_id, tenant_ctx=tenant_ctx)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="connector secret storage failed",
        ) from exc

    # Index the operations for capability search. Reported honestly: this used
    # to be wrapped in suppress(Exception) while tools_imported claimed success.
    capabilities_indexed = 0
    db = getattr(request.app.state, "db_session_factory", None)
    if db:
        from app.mcp.openapi_importer import persist_tools

        for tool in tools:
            tool["connector_id"] = server_id
            tool["tenant_id"] = tenant_ctx.tenant_id
        capabilities_indexed = await persist_tools(tools, db, tenant_ctx.tenant_id)

    return {
        "server_id": server_id,
        "name": connector_name,
        "tools_imported": len(tool_defs),
        "capabilities_indexed": capabilities_indexed,
        "base_url": body.base_url,
    }


# ── Capability Registry API ───────────────────────────────────────────────────


CAPABILITIES_PAGE_MAX = 500


@router.get("/capabilities")
async def list_capabilities(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=CAPABILITIES_PAGE_MAX),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> list[dict]:
    """One page of the tenant's discovered tool capabilities (a02-F032-05).

    It returned every row for the tenant with no LIMIT. Pages are ordered by
    (tool_name, connector_id); ``X-Next-Offset`` is set when there is more.
    """
    tenant_ctx = _require_tenant(request)
    try:
        # Resolved inside the try: a factory that cannot be built is the same
        # "capability index unavailable" 503 as a failing query (it was a 500).
        db = getattr(request.app.state, "db_session_factory", None)
        if db is None:
            from app.db.session import get_session_factory

            db = get_session_factory()
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
            sql = (
                "SELECT tool_name, connector_id, description, risk_level, "
                "health_status, success_rate, avg_latency_ms "
                "FROM tool_capabilities WHERE tenant_id = :tid"
            )
            params: dict = {"tid": tenant_ctx.tenant_id}
            if q:
                sql += " AND (tool_name ILIKE :q OR description ILIKE :q)"
                params["q"] = f"%{q}%"
            sql += " ORDER BY tool_name, connector_id LIMIT :lim OFFSET :off"
            params.update(lim=limit + 1, off=offset)
            result = await session.execute(text(sql), params)
            rows = list(result.fetchall())
        if len(rows) > limit:
            rows = rows[:limit]
            response.headers["X-Next-Offset"] = str(offset + limit)
        return [
            {
                "tool_name": r[0],
                "connector_id": r[1],
                "description": r[2],
                "risk_level": r[3],
                "health_status": r[4],
                "success_rate": round(r[5], 3),
                "avg_latency_ms": round(r[6], 1),
            }
            for r in rows
        ]
    except Exception as exc:
        # This used to return the first 20 CONNECTOR_CATALOG entries dressed up
        # as this tenant's discovered tools on ANY error — connector names as
        # tool names, for connectors the tenant never registered. Say it failed.
        _logger.warning("capabilities_query_failed error=%s", str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="capability index unavailable",
        ) from exc


CAPABILITY_SEARCH_RATE_LIMIT = 30  # requests per tenant ...
CAPABILITY_SEARCH_WINDOW_S = 60  # ... per this many seconds


async def _capability_rate_limit(request: Request, tenant_id: str) -> None:
    """Live discovery of every connector is costly: bound it per tenant (a02-F032-07)."""
    from app.tenancy.ip_rate_limit import enforce_key_rate_limit

    await enforce_key_rate_limit(
        f"capability_search:{tenant_id}",
        bucket="capability_search",
        limit=CAPABILITY_SEARCH_RATE_LIMIT,
        window_s=CAPABILITY_SEARCH_WINDOW_S,
        redis=getattr(request.app.state, "_redis", None),
        detail="Too many capability searches for this tenant. Try again shortly.",
    )


async def _discovery_report(request: Request, tenant_ctx: Any) -> Any:
    """Live tools of every connector, with per-connector errors (a02-F032-02/06).

    No MCP client or an unreadable connector list is a 503 — never an empty
    result standing in for an error.
    """
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        raise HTTPException(status_code=503, detail="MCP client not available")
    try:
        return await mcp_client.discover_all_tools_report(tenant_ctx=tenant_ctx)
    except Exception as exc:
        _logger.warning("capability_discovery_failed error=%s", str(exc)[:200])
        raise HTTPException(
            status_code=503, detail="Connector list unavailable; tool discovery failed"
        ) from exc


@router.get("/capabilities/search")
async def search_capabilities(
    request: Request, q: str = Query(..., min_length=1, max_length=500)
) -> dict:
    """Semantic + keyword search over the tenant's live connector tools.

    A connector whose discovery fails is listed in ``connector_errors`` and
    ``complete`` is false (it used to read as "no tools"). Rate-limited per
    tenant; descriptor embeddings are cached and new ones are charged to the
    tenant's budget (429 when exhausted, 503 when it cannot be verified).
    """
    tenant_ctx = _require_tenant(request)
    await _capability_rate_limit(request, tenant_ctx.tenant_id)
    report = await _discovery_report(request, tenant_ctx)

    from app.embedding.metering import (
        EmbeddingBudgetExceededError,
        EmbeddingBudgetUnverifiableError,
        resolve_cost_controller,
    )
    from app.mcp.capability_search import CapabilitySearch

    embedder = getattr(request.app.state, "embedder", None)
    try:
        search = CapabilitySearch(
            tools=report.tools,
            embedder=embedder,
            cost_controller=resolve_cost_controller(request.app.state)
            if embedder is not None
            else None,
            meter=True,
        )
        results = await search.search(q, tenant_ctx=tenant_ctx, top_k=10)
    except EmbeddingBudgetExceededError as exc:
        raise HTTPException(status_code=429, detail="LLM budget exhausted for this tenant") from exc
    except EmbeddingBudgetUnverifiableError as exc:
        raise HTTPException(
            status_code=503, detail="Budget state could not be verified; search refused"
        ) from exc
    return {
        "query": q,
        "results": [r.to_dict() if hasattr(r, "to_dict") else r for r in results],
        "complete": report.complete,
        "connector_errors": report.errors,
    }


@router.post("/{server_id}/discover")
async def discover_connector_tools(request: Request, server_id: str) -> dict:
    """Discover and persist all tools from a registered connector."""
    tenant_ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        raise HTTPException(503, "MCP client not available")

    try:
        tools = await mcp_client.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
    except Exception as exc:
        raise HTTPException(500, f"Discovery failed: {exc}") from exc

    if not tools:
        return {"server_id": server_id, "tools_discovered": 0, "tools_saved": 0}
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tools were discovered but cannot be saved (no database configured).",
        )

    # Counted only once the transaction has committed (OAPI-01): a rolled-back
    # write used to answer 200 with tools_saved = the rows it had attempted.
    saved = 0
    attempted = 0
    if db is not None:
        try:
            import json
            import uuid

            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                for tool in tools:
                    await session.execute(
                        text(
                            """
                            INSERT INTO tool_capabilities
                                (id, tenant_id, connector_id, tool_name,
                                 description, http_path, input_schema, risk_level,
                                 last_discovered)
                            VALUES
                                (:id, :tid, :cid, :name,
                                 :desc, :path, CAST(:schema AS jsonb), :risk,
                                 NOW())
                            ON CONFLICT (tenant_id, connector_id, tool_name)
                            DO UPDATE SET
                                description    = EXCLUDED.description,
                                input_schema   = EXCLUDED.input_schema,
                                last_discovered = NOW(),
                                updated_at     = NOW()
                            """
                        ),
                        {
                            "id": uuid.uuid4().hex,
                            "tid": tenant_ctx.tenant_id,
                            "cid": server_id,
                            "name": getattr(tool, "name", str(tool)),
                            "desc": getattr(tool, "description", ""),
                            # ToolDefinition (app/mcp/client.py) has no http_path field --
                            # MCP-discovered tools aren't necessarily HTTP endpoints, unlike
                            # the OpenAPI importer's tools. http_path is NOT NULL with no
                            # column default (migration 0020), so this INSERT previously
                            # raised NotNullViolationError on every call.
                            "path": getattr(tool, "http_path", "") or "",
                            "schema": json.dumps(getattr(tool, "input_schema", {})),
                            "risk": getattr(tool, "risk_level", "low"),
                        },
                    )
                    attempted += 1
            saved = attempted
        except Exception as exc:
            _logger.error(
                "tool_capability_persist_failed tenant=%s server=%s error=%s",
                tenant_ctx.tenant_id,
                server_id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"{len(tools)} tools were discovered but could not be saved; "
                    "nothing was persisted. Retry shortly."
                ),
            ) from exc

    return {
        "server_id": server_id,
        "tools_discovered": len(tools),
        "tools_saved": saved,
    }


def _catalog_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _catalog_phrases(spec: Any) -> set[str]:
    """How a goal may name a catalog connector ('google-sheets' → 'google sheets')."""
    phrases = {
        re.sub(r"[-_\s]+", " ", str(v).lower()).strip()
        for v in (spec.name, getattr(spec, "display_name", ""))
        if v
    }
    return {p for p in phrases if p}


def _goal_names(goal: str, spec: Any) -> bool:
    """True when the goal names the connector as a whole word / phrase.

    It used to be a substring test ('git' matched 'digital', 'box' matched
    'inbox')."""
    text = re.sub(r"[-_\s]+", " ", goal.lower())
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text)
        for phrase in _catalog_phrases(spec)
    )


def _registered_catalog_keys(configs: list[Any]) -> set[str]:
    """Catalog keys of the tenant's registered connectors (name, built-in type)."""
    from app.mcp.servers.registry_wiring import builtin_type_of

    keys: set[str] = set()
    for cfg in configs:
        keys.add(_catalog_key(str(getattr(cfg, "name", "") or "")))
        builtin = builtin_type_of(cfg)
        if builtin:
            keys.add("builtin:" + builtin)
            keys.add(_catalog_key(builtin.removeprefix("builtin-")))
    return keys


@router.get("/capabilities/missing")
async def missing_capabilities(
    request: Request, goal: str = Query(..., min_length=1, max_length=10_000)
) -> dict:
    """Catalog connectors the goal names that the tenant has not registered.

    a02-F032-01: catalog names used to be matched as substrings of the goal and
    compared with TOOL names (never equal to a connector name), and
    ``can_proceed`` was true whenever any tool existed. Now the goal must name a
    connector as a word or phrase, it is missing when no registered connector is
    of that type (by name or built-in type), and ``can_proceed`` is true only
    when nothing the goal names is missing and discovery of the registered
    connectors was complete (a02-F032-02: failures are reported, not hidden).
    """
    tenant_ctx = _require_tenant(request)
    await _capability_rate_limit(request, tenant_ctx.tenant_id)
    try:
        configs = await _registry(request).list_servers(tenant_ctx=tenant_ctx)
    except Exception as exc:
        _logger.warning("capability_missing_registry_failed error=%s", str(exc)[:200])
        raise HTTPException(status_code=503, detail="Connector list unavailable") from exc
    report = await _discovery_report(request, tenant_ctx)

    registered = _registered_catalog_keys(configs)
    suggestions: list[dict] = []
    for spec in CONNECTOR_CATALOG:
        if not _goal_names(goal, spec):
            continue
        installed = (
            _catalog_key(spec.name) in registered
            or _catalog_key(getattr(spec, "display_name", "") or "") in registered
            or (bool(spec.builtin_server_id) and f"builtin:{spec.builtin_server_id}" in registered)
        )
        if not installed:
            suggestions.append(
                {
                    "connector": spec.name,
                    "category": getattr(spec, "category", "") or "integration",
                    "install_hint": (
                        f"Register the {spec.name} connector to enable this capability"
                    ),
                }
            )

    return {
        "goal": goal,
        "available_tool_count": len(report.tools),
        "missing_connectors": suggestions[:5],
        "can_proceed": not suggestions and report.complete,
        "complete": report.complete,
        "connector_errors": report.errors,
    }


# ── Connector certification (a02-F031-02) ─────────────────────────────────────
# app/mcp/certification.py (static manifest checks and a discovery + read-call
# check against a connector) was referenced only by its tests. These routes
# expose it as the certification design specified (targets + run).


class CertificationRunRequest(BaseModel):
    connector: str = Field(..., min_length=1, max_length=64)
    level: Literal["static", "mocked"] = "static"
    # Required for level=mocked: one of the caller's connectors to exercise.
    server_id: str | None = Field(default=None, min_length=1, max_length=128)


@router.get("/certification/targets")
async def certification_targets(request: Request) -> list[dict[str, Any]]:
    """The connectors of the certification manifest (no secrets, only shape)."""
    _require_tenant(request)
    from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS

    return [
        {
            "connector": key,
            "display_name": target["display_name"],
            "category": target["category"],
            "auth_modes": list(target["auth_modes"]),
            "read_tool": target["read_tool"],
            "expected_artifact_kind": target["expected_artifact_kind"],
        }
        for key, target in sorted(CONNECTOR_CERTIFICATION_TARGETS.items())
    ]


@router.post("/certification/run")
async def run_certification(request: Request, body: CertificationRunRequest) -> dict[str, Any]:
    """Certify a connector: ``static`` checks the manifest entry; ``mocked``
    discovers the caller's connector ``server_id`` and runs the manifest's
    read-only tool on it. Unknown connector → the result says failed; another
    tenant's (or an unknown) connector → 404."""
    tenant_ctx = _require_tenant(request)
    from app.mcp.certification import run_mocked_certification, run_static_certification

    if body.level == "static":
        return run_static_certification(body.connector)
    if not body.server_id:
        raise HTTPException(status_code=422, detail="server_id is required for level=mocked")
    if await _registry(request).get(body.server_id, tenant_ctx=tenant_ctx) is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        raise HTTPException(status_code=503, detail="MCP client not available")
    return await run_mocked_certification(
        body.connector, mcp_client=mcp_client, server_id=body.server_id, tenant_ctx=tenant_ctx
    )


# ── Single-connector reads (connector detail page) ────────────────────────────
# Declared LAST so the literal GET routes above (/catalog, /capabilities,
# /oauth/start, …) win over the /{server_id} path parameter.


@router.get("/{server_id}/tools")
async def list_connector_tools(request: Request, server_id: str) -> list[dict[str, Any]]:
    """Live tool list of one of the caller's connectors (MCP ``tools/list``,
    built-in definitions or OpenAPI operations, via the MCP client).

    404 when the caller has no such connector; 503 without an MCP client; 502
    when discovery fails — never an empty list standing in for an error.
    """
    tenant_ctx = _require_tenant(request)
    if await _registry(request).get(server_id, tenant_ctx=tenant_ctx) is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        raise HTTPException(status_code=503, detail="MCP client not available")
    from app.mcp.client import strict_discovery

    try:
        # Strict: a transport / protocol failure is the 502 this route promises,
        # not an empty tool list (a02-F032-02).
        with strict_discovery():
            tools = await mcp_client.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
    except Exception as exc:
        _logger.warning("connector_tools_discovery_failed server=%s: %s", server_id, exc)
        raise HTTPException(status_code=502, detail=f"Tool discovery failed: {exc}") from exc
    return [
        {
            "name": getattr(t, "name", ""),
            "description": getattr(t, "description", ""),
            "input_schema": getattr(t, "input_schema", {}) or {},
        }
        for t in tools
    ]


@router.get("/{server_id}")
async def get_connector(request: Request, server_id: str) -> dict[str, Any]:
    """One of the caller's connectors, with secrets masked (404 otherwise)."""
    tenant_ctx = _require_tenant(request)
    cfg = await _registry(request).get(server_id, tenant_ctx=tenant_ctx)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _public_connector(server_id, cfg)
