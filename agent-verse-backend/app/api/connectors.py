"""Connectors API — register, list, and manage MCP server connections."""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.mcp.catalog import CONNECTOR_CATALOG
from app.mcp.registry import MCPServerConfig
from app.providers.vault import (
    connector_secret_ref,
    is_connector_secret_ref,
    resolve_connector_secret_ref,
    resolve_connector_secret_ref_for_tenant,
    store_connector_secret_for_tenant,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])
_REDACTED = "<redacted>"
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


class RegisterConnectorRequest(BaseModel):
    name: str
    url: str
    auth_type: str
    auth_config: dict[str, Any] = {}
    description: str = ""
    priority: int = 0


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _registry(request: Request) -> Any:
    return request.app.state.mcp_registry


def _is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "development").lower() == "production"


def _normalize_auth_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_auth_key(key: str) -> bool:
    normalized = _normalize_auth_key(key)
    return any(part in normalized for part in _SENSITIVE_AUTH_KEY_PARTS)


def _auth_config_requires_secret_storage(auth_config: dict[str, Any]) -> bool:
    return any(
        _is_sensitive_auth_key(key)
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
    if (
        needs_secret_storage
        and _is_production()
        and not production_safe
    ):
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
            return resolve_connector_secret_ref(value) or ""
        resolved = secret_resolver(value)
        if hasattr(resolved, "__await__"):
            resolved = await resolved
        return str(resolved) if resolved is not None else ""
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
    return url.rstrip("/").endswith("/mcp")


def _mask_auth_config(auth_config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _REDACTED
        if _is_sensitive_auth_key(key) or is_connector_secret_ref(value)
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


def _public_connector(server_id: str, cfg: MCPServerConfig) -> dict[str, Any]:
    data = cfg.model_dump(exclude={"server_id"})
    data["auth_config"] = _mask_auth_config(dict(cfg.auth_config))
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
async def get_catalog(request: Request) -> list[dict[str, Any]]:
    _require_tenant(request)
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "auth_type": spec.auth_type,
            "default_url": spec.default_url,
        }
        for spec in CONNECTOR_CATALOG
    ]


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
async def register_connector(
    request: Request, body: RegisterConnectorRequest
) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    secret_store = _connector_secret_store(
        request,
        needs_secret_storage=_auth_config_requires_secret_storage(body.auth_config),
    )
    pending_secrets: dict[str, str] = {}

    def _config_for(server_id: str) -> MCPServerConfig:
        return MCPServerConfig(
            server_id=server_id,  # preserve the registry-generated ID
            name=body.name,
            url=body.url,
            auth_type=body.auth_type,
            auth_config=_store_sensitive_auth_refs(
                server_id,
                body.auth_config,
                pending_secrets,
            ),
            description=body.description,
            priority=body.priority,
        )

    server_id = await reg.register(_config_for, tenant_ctx=tenant_ctx)
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
    return {"server_id": server_id, "name": body.name, "url": body.url}


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
    auth_config = _preserve_redacted_auth_config(body.auth_config, dict(existing.auth_config))
    secret_store = _connector_secret_store(
        request,
        needs_secret_storage=_auth_config_requires_secret_storage(auth_config),
    )
    pending_secrets: dict[str, str] = {}
    stored_auth_config = _store_sensitive_auth_refs(server_id, auth_config, pending_secrets)
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
    cfg = MCPServerConfig(
        name=body.name,
        url=body.url,
        auth_type=body.auth_type,
        auth_config=stored_auth_config,
        description=body.description,
        priority=body.priority,
    )
    updated = await reg.update(server_id, cfg, tenant_ctx=tenant_ctx)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )
    return _public_connector(server_id, cfg)


@router.post("/{server_id}/test")
async def test_connector(request: Request, server_id: str) -> dict[str, Any]:
    """Test connectivity to a registered MCP server by calling its /health endpoint."""
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)

    cfg = await reg.get(server_id, tenant_ctx=tenant_ctx)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector {server_id} not found",
        )

    t0 = time.monotonic()
    try:
        headers = await _build_auth_headers(cfg, secret_resolver=_secret_resolver(request))
        async with httpx.AsyncClient(timeout=5.0) as client:
            if _is_mcp_endpoint(cfg.url):
                headers = {
                    **headers,
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                }
                resp = await client.post(
                    cfg.url.rstrip("/"),
                    json=_mcp_initialize_payload(),
                    headers=headers,
                )
            else:
                resp = await client.get(f"{cfg.url.rstrip('/')}/health", headers=headers)
            latency_ms = round((time.monotonic() - t0) * 1000)
            status_code = resp.status_code
            reachable = 200 <= status_code < 300
            if status_code in {401, 403}:
                status_label = "auth_failed"
            elif reachable:
                status_label = "healthy"
            elif status_code < 500:
                status_label = "bad_response"
            else:
                status_label = "unreachable"
    except Exception as exc:
        latency_ms = round((time.monotonic() - t0) * 1000)
        reachable = False
        status_code = None
        status_label = "unreachable"
        error = str(exc)
    else:
        error = resp.text[:200] if not reachable else ""

    result = {
        "server_id": server_id,
        "name": cfg.name,
        "url": cfg.url,
        "reachable": reachable,
        "status": status_label,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "error": error,
    }

    # Persist health snapshot
    db = getattr(request.app.state, "db_session_factory", None)
    if db:
        try:
            from app.db.models.mcp import ConnectorHealthSnapshot
            from app.db.rls import sqlalchemy_rls_context
            async with db() as session, session.begin(), \
                       sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                session.add(ConnectorHealthSnapshot(
                    server_id=server_id,
                    tenant_id=tenant_ctx.tenant_id,
                    status=result.get("status", "unknown"),
                    latency_ms=result.get("latency_ms"),
                    error=result.get("error") or None,
                ))
        except Exception:
            pass

    return result


@router.get("/{server_id}/health")
async def get_connector_health_history(
    request: Request, server_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Return health check history for a connector."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import select

        from app.db.models.mcp import ConnectorHealthSnapshot
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
            result = await session.execute(
                select(ConnectorHealthSnapshot)
                .where(ConnectorHealthSnapshot.server_id == server_id,
                       ConnectorHealthSnapshot.tenant_id == tenant.tenant_id)
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
    except Exception:
        return []


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

    # Start the PKCE flow — generates state token + code challenge
    pkce_params = oauth_manager.start_flow(server_id=server_id, tenant_ctx=tenant_ctx)

    # Build the full authorization URL
    authorize_url = cfg.auth_config.get("authorize_url", "")
    client_id = cfg.auth_config.get("client_id", "")
    redirect_uri = (
        str(request.base_url).rstrip("/")
        + f"/connectors/oauth/callback?server_id={server_id}"
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


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    server_id: str = "",
    redirect_uri: str = "http://localhost:8000/connectors/oauth/callback",
) -> dict[str, Any]:
    """OAuth callback — exchange authorization code for access tokens via PKCE."""
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

    # Actually exchange the authorization code for tokens
    try:
        token = await oauth_manager.exchange_code(
            code=code,
            state=state,
            token_url=token_url,
            client_id=client_id,
            redirect_uri=redirect_uri,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        return {"server_id": server_id, "status": "error", "message": str(exc)}

    if token is None:
        return {
            "server_id": server_id,
            "status": "error",
            "message": "Invalid OAuth state parameter — flow may have expired",
        }

    # Encrypt and persist tokens in the credential vault via auth_config
    from app.providers.vault import get_vault

    vault = get_vault()
    encrypted_access = vault.encrypt(token.access_token)
    encrypted_refresh = vault.encrypt(token.refresh_token) if token.refresh_token else ""

    if cfg is not None:
        from app.mcp.registry import MCPServerConfig

        updated_config = dict(cfg.auth_config)
        updated_config["_encrypted_access_token"] = encrypted_access
        updated_config["_encrypted_refresh_token"] = encrypted_refresh
        updated_config["_token_scope"] = token.scope
        updated_config["_token_type"] = token.token_type

        updated_cfg = MCPServerConfig(
            name=cfg.name,
            url=cfg.url,
            auth_type=cfg.auth_type,
            auth_config=updated_config,
            description=cfg.description,
            priority=cfg.priority,
        )
        # Re-register with updated config (registry has no in-place update)
        await reg.unregister(server_id, tenant_ctx=tenant_ctx)
        await reg.register(updated_cfg, tenant_ctx=tenant_ctx)

    return {
        "server_id": server_id,
        "status": "connected",
        "message": "OAuth tokens stored securely in credential vault.",
        "token_type": token.token_type,
        "scope": token.scope,
        "has_refresh_token": bool(token.refresh_token),
    }


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_connector(request: Request, server_id: str) -> None:
    tenant_ctx = _require_tenant(request)
    reg = _registry(request)
    removed = await reg.unregister(server_id, tenant_ctx=tenant_ctx)
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
    auth_type: str = "bearer"
    auth_config: dict[str, Any] = {}
    description: str = ""


@router.post("/import-openapi", status_code=status.HTTP_201_CREATED)
async def import_openapi_connector(
    request: Request, body: OpenAPIImportRequest
) -> dict[str, Any]:
    """Import an OpenAPI 3.x spec and register it as a connector with extracted tools."""
    tenant_ctx = _require_tenant(request)

    from app.mcp.openapi_importer import extract_tools_from_spec, parse_openapi_spec

    try:
        spec = parse_openapi_spec(body.openapi_spec)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot parse OpenAPI spec: {exc}",
        ) from exc

    import uuid
    placeholder_id = uuid.uuid4().hex
    tools = extract_tools_from_spec(
        spec, connector_id=placeholder_id, tenant_id=tenant_ctx.tenant_id
    )

    # Normalise auth_type to a known Literal (default to bearer for unknown types)
    _VALID_AUTH_TYPES = {
        "bearer", "api_key", "oauth_ac", "oauth_cc", "pkce",
        "basic", "custom_header", "mtls", "hmac",
    }
    safe_auth_type: Any = body.auth_type if body.auth_type in _VALID_AUTH_TYPES else "bearer"

    connector_name = body.name or (spec.get("info", {}).get("title") or "Imported API")
    connector_desc = (
        body.description
        or f"Auto-imported from OpenAPI spec ({len(tools)} endpoints)"
    )

    cfg = MCPServerConfig(
        name=connector_name,
        url=body.base_url,
        auth_type=safe_auth_type,
        auth_config=body.auth_config,
        description=connector_desc,
    )

    reg = _registry(request)
    server_id = await reg.register(cfg, tenant_ctx=tenant_ctx)

    # Optionally persist tool definitions (best-effort, non-fatal)
    db = getattr(request.app.state, "db_session_factory", None)
    if db and tools:
        from app.mcp.openapi_importer import persist_tools
        for tool in tools:
            tool["connector_id"] = server_id
            tool["tenant_id"] = tenant_ctx.tenant_id
        try:
            await persist_tools(tools, db, tenant_ctx.tenant_id)
        except Exception:
            pass

    return {
        "server_id": server_id,
        "name": connector_name,
        "tools_imported": len(tools),
        "base_url": body.base_url,
    }


# ── Capability Registry API ───────────────────────────────────────────────────


@router.get("/capabilities")
async def list_capabilities(request: Request, q: str = "") -> list[dict]:
    """List all discovered tool capabilities for this tenant."""
    tenant_ctx = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        from app.db.session import get_session_factory
        db = get_session_factory()
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        async with db() as session:
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                sql = (
                    "SELECT tool_name, connector_id, description, risk_level, "
                    "health_status, success_rate, avg_latency_ms "
                    "FROM tool_capabilities WHERE tenant_id = :tid"
                )
                params: dict = {"tid": tenant_ctx.tenant_id}
                if q:
                    sql += " AND (tool_name ILIKE :q OR description ILIKE :q)"
                    params["q"] = f"%{q}%"
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
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
    except Exception:
        # Fall back to catalog when DB unavailable
        from app.mcp.catalog import CONNECTOR_CATALOG
        return [
            {
                "tool_name": c.name,
                "connector_id": c.name,
                "description": c.description,
                "risk_level": "unknown",
                "health_status": "unknown",
            }
            for c in CONNECTOR_CATALOG[:20]
        ]


@router.get("/capabilities/search")
async def search_capabilities(request: Request, q: str = Query(...)) -> dict:
    """Semantic + keyword search over discovered capabilities."""
    tenant_ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)

    all_tools: list = []
    if mcp_client is not None:
        try:
            all_tools = await mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)
        except Exception:
            pass

    from app.mcp.capability_search import CapabilitySearch
    embedder = getattr(request.app.state, "embedder", None)
    search = CapabilitySearch(tools=all_tools, embedder=embedder)
    results = await search.search(q, top_k=10)
    return {
        "query": q,
        "results": [r.to_dict() if hasattr(r, "to_dict") else r for r in results],
    }


@router.post("/{server_id}/discover")
async def discover_connector_tools(request: Request, server_id: str) -> dict:
    """Discover and persist all tools from a registered connector."""
    tenant_ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        raise HTTPException(503, "MCP client not available")

    try:
        tools = await mcp_client.discover_tools(
            server_id=server_id, tenant_ctx=tenant_ctx
        )
    except Exception as exc:
        raise HTTPException(500, f"Discovery failed: {exc}")

    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        from app.db.session import get_session_factory
        db = get_session_factory()

    saved = 0
    if db is not None:
        try:
            import json
            import uuid

            from sqlalchemy import text
            async with db() as session, session.begin():
                for tool in tools:
                    await session.execute(
                        text(
                            """
                            INSERT INTO tool_capabilities
                                (id, tenant_id, connector_id, tool_name,
                                 description, input_schema, risk_level,
                                 last_discovered)
                            VALUES
                                (:id, :tid, :cid, :name,
                                 :desc, :schema::jsonb, :risk,
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
                            "schema": json.dumps(
                                getattr(tool, "input_schema", {})
                            ),
                            "risk": getattr(tool, "risk_level", "low"),
                        },
                    )
                    saved += 1
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "tool_capability_persist_failed: %s", exc
            )

    return {
        "server_id": server_id,
        "tools_discovered": len(tools),
        "tools_saved": saved,
    }


@router.get("/capabilities/missing")
async def missing_capabilities(
    request: Request, goal: str = Query(...)
) -> dict:
    """Identify capabilities needed for a goal but not yet available."""
    tenant_ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)

    available_tools: list = []
    if mcp_client is not None:
        try:
            available_tools = await mcp_client.discover_all_tools(
                tenant_ctx=tenant_ctx
            )
        except Exception:
            pass

    available_names = {getattr(t, "name", "") for t in available_tools}

    suggestions: list[dict] = []
    goal_lower = goal.lower()
    for spec in CONNECTOR_CATALOG:
        name_words = spec.name.lower().replace("-", " ").replace("_", " ")
        if name_words in goal_lower or spec.name.lower() in goal_lower:
            if spec.name not in available_names:
                suggestions.append(
                    {
                        "connector": spec.name,
                        "category": "integration",
                        "install_hint": (
                            f"Register the {spec.name} connector "
                            "to enable this capability"
                        ),
                    }
                )

    return {
        "goal": goal,
        "available_tool_count": len(available_tools),
        "missing_connectors": suggestions[:5],
        "can_proceed": len(suggestions) == 0 or len(available_tools) > 0,
    }
