"""Connectors API — register, list, and manage MCP server connections."""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.mcp.catalog import CONNECTOR_CATALOG
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.net.ssrf_guard import SSRFError, assert_public_url
from app.providers.vault import (
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

_BUILTIN_HANDLER_CACHE: dict[str, object] | None = None


def _get_builtin_handler_for_name(connector_name: str):
    """Return the builtin handler callable for connector_name, or None."""
    global _BUILTIN_HANDLER_CACHE
    if _BUILTIN_HANDLER_CACHE is None:
        try:
            from app.mcp.servers.registry_wiring import get_builtin_server_configs

            _BUILTIN_HANDLER_CACHE = {
                cfg["name"].lower(): cfg["handler"] for cfg in get_builtin_server_configs()
            }
        except Exception:
            _BUILTIN_HANDLER_CACHE = {}
    return _BUILTIN_HANDLER_CACHE.get(connector_name.lower().strip())


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
    from app.api._deps import get_mcp_registry as _gmr

    return _gmr(request)


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
        key: _REDACTED if _is_sensitive_auth_key(key) or is_connector_secret_ref(value) else value
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
    # Expose whether this connector has a native builtin Python handler
    # so the frontend can show the ⚡ Built-in badge on registered connectors.
    from app.mcp.registry import MCPRegistry as _MCPReg

    data["has_builtin"] = (
        cfg.builtin_handler is not None or _MCPReg.get_builtin_handler(server_id) is not None
    )
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
    # SSRF guard: reject private/loopback/cloud-metadata URLs at registration time.
    if body.url and body.url != "builtin://":
        try:
            assert_public_url(body.url, context="connector registration")
        except SSRFError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connector URL rejected by SSRF guard: {exc}",
            ) from exc
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

    # Auto-assign builtin handler when this connector matches a known builtin type.
    builtin_handler = _get_builtin_handler_for_name(body.name)
    if builtin_handler is not None:
        MCPRegistry.register_builtin_handler(server_id, builtin_handler)

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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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

    # ── Resolve vault secret references in auth_config ─────────────────────
    # Tokens are stored as "secret://connector/<id>/token" vault refs.
    # Direct test functions call _get_cred() which skips vault refs,
    # so we must resolve them here before dispatching.
    resolved_auth_config: dict[str, Any] = {}
    secret_store = _connector_secret_store(request)
    for key, value in (cfg.auth_config or {}).items():
        if isinstance(value, str) and is_connector_secret_ref(value):
            try:
                plain = await resolve_connector_secret_ref_for_tenant(
                    value, store=secret_store, tenant_ctx=tenant
                )
                resolved_auth_config[key] = plain or value
            except Exception:
                resolved_auth_config[key] = value
        else:
            resolved_auth_config[key] = value
    # Overlay resolved values onto a copy of cfg so test functions see plain text
    cfg = cfg.model_copy(update={"auth_config": resolved_auth_config})

    connector_name = cfg.name.lower().strip()

    # ── 1. Direct REST test (primary path) ────────────────────────────────────
    direct_test_fn = _DIRECT_REST_TESTS.get(connector_name)
    if direct_test_fn:
        return await direct_test_fn(cfg, started, server_id)

    # ── 2. mcp_client.call_tool (for connectors with an MCP tool entry) ───────
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is None:
        from app.mcp.client import MCPClient

        mcp_client = MCPClient(registry=registry)

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
    if not url or url == "builtin://":
        return {"server_id": server_id, "reachable": True, "status": "not_tested", "latency_ms": 0}

    try:
        # SSRF protection: validate the URL before making any outbound request.
        # Never allow requests to internal/metadata endpoints.
        try:
            assert_public_url(url, context="connector test")
        except SSRFError as ssrf_exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SSRF protection: disallowed URL",
            ) from ssrf_exc

        # Build auth headers from connector config — never forward the incoming
        # request's own Authorization header to external services.
        headers: dict[str, str] = {}
        for key, value in (cfg.auth_config or {}).items():
            if isinstance(value, str) and (
                "token" in key.lower() or "authorization" in key.lower()
            ):
                headers["Authorization"] = f"Bearer {value}"
                break
        async with httpx.AsyncClient(timeout=10.0) as hclient:
            resp = await hclient.get(url, headers=headers)
        latency_ms = round((time.time() - started) * 1000)
        reachable = resp.status_code < 500
        return {
            "server_id": server_id,
            "reachable": reachable,
            "status": "passed" if reachable else "failed",
            "latency_ms": latency_ms,
            "http_status": resp.status_code,
        }
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
    except Exception:
        return []


# ---------------------------------------------------------------------------
# OAuth popup flow — POST endpoints (connector_name-based, for the frontend
# popup button flow). These are distinct from the PKCE GET endpoints above,
# which operate on already-registered connectors by server_id.
# ---------------------------------------------------------------------------

# Short-lived in-memory state store — production should use Redis with TTL.
# State token lifetime: 10 minutes.
_OAUTH_STATE_TTL = 600  # seconds
_oauth_states: dict[str, dict[str, Any]] = {}


def _cleanup_oauth_states() -> None:
    """Remove expired OAuth state tokens."""
    cutoff = time.time() - _OAUTH_STATE_TTL
    expired = [k for k, v in _oauth_states.items() if v.get("created_at", 0) < cutoff]
    for k in expired:
        del _oauth_states[k]


class OAuthStartBody(BaseModel):
    connector_name: str


@router.post("/oauth/start")
async def start_oauth_popup(request: Request, body: OAuthStartBody) -> dict[str, Any]:
    """Start an OAuth popup flow.

    Returns an authorization URL the frontend should open in a popup window, along
    with a CSRF state token the frontend must validate in the callback.
    """
    import secrets
    import urllib.parse

    tenant = _require_tenant(request)
    connector_name = body.connector_name.lower().strip()

    _cleanup_oauth_states()

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "tenant_id": tenant.tenant_id,
        "connector_name": connector_name,
        "created_at": time.time(),
    }

    # Derive redirect_uri from settings or request base URL
    settings = getattr(request.app.state, "settings", None)
    frontend_url = (getattr(settings, "frontend_url", "") or "").rstrip("/")
    if not frontend_url:
        frontend_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{frontend_url}/connectors/oauth/callback"

    # Connector-specific authorization URLs — placeholders for unconfigured credentials.
    def _client_id(env_key: str) -> str:
        return getattr(settings, env_key, "") or ""

    oauth_urls: dict[str, str] = {
        "github": (
            "https://github.com/login/oauth/authorize?"
            + urllib.parse.urlencode(
                {
                    "client_id": _client_id("GITHUB_CLIENT_ID"),
                    "scope": "repo,read:org",
                    "state": state,
                    "redirect_uri": redirect_uri,
                }
            )
        ),
        "slack": (
            "https://slack.com/oauth/v2/authorize?"
            + urllib.parse.urlencode(
                {
                    "client_id": _client_id("SLACK_CLIENT_ID"),
                    "scope": "channels:read,chat:write",
                    "state": state,
                    "redirect_uri": redirect_uri,
                }
            )
        ),
        "google": (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urllib.parse.urlencode(
                {
                    "client_id": _client_id("GOOGLE_CLIENT_ID"),
                    "response_type": "code",
                    "scope": "email profile",
                    "state": state,
                    "redirect_uri": redirect_uri,
                }
            )
        ),
        "jira": (
            "https://auth.atlassian.com/authorize?"
            + urllib.parse.urlencode(
                {
                    "audience": "api.atlassian.com",
                    "client_id": _client_id("JIRA_CLIENT_ID"),
                    "scope": "read:jira-work",
                    "state": state,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "prompt": "consent",
                }
            )
        ),
    }

    auth_url = oauth_urls.get(connector_name)
    if not auth_url:
        # Generic placeholder so the popup flow still works for unknown connectors
        auth_url = "https://example.com/oauth?" + urllib.parse.urlencode(
            {"state": state, "redirect_uri": redirect_uri}
        )

    return {"auth_url": auth_url, "state": state}


class OAuthCallbackBody(BaseModel):
    code: str
    state: str
    connector_name: str


@router.post("/oauth/callback")
async def complete_oauth_popup(request: Request, body: OAuthCallbackBody) -> dict[str, Any]:
    """Complete the OAuth popup flow.

    Validates the state token, (in production) exchanges the code for an access
    token, and registers the connector for the tenant.

    # NOTE: This endpoint currently does NOT perform the OAuth token exchange.
    # The authorization code is received but not exchanged for an access token.
    # To enable real OAuth, implement the token exchange for each connector type.
    # See: https://tools.ietf.org/html/rfc6749#section-4.1.3
    """
    import uuid

    tenant = _require_tenant(request)

    _cleanup_oauth_states()

    state_data = _oauth_states.pop(body.state, None)
    if state_data is None or state_data.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state token")

    connector_name = body.connector_name.lower().strip() or state_data.get("connector_name", "")
    server_id = f"{connector_name}-oauth-{uuid.uuid4().hex[:8]}"

    # Determine whether real OAuth credentials are configured for this connector.
    settings = getattr(request.app.state, "settings", None)
    oauth_client_secret = getattr(settings, f"{connector_name.upper()}_CLIENT_SECRET", None)
    if not oauth_client_secret:
        # No OAuth credentials configured — store a pending state so the UI can
        # prompt the admin to configure credentials rather than silently using a
        # fake token that will never work against a real API.
        auth_config: dict[str, str] = {
            "status": "pending_oauth",
            "oauth_code": body.code[:4] + "****",
        }
        _logger.warning(
            "OAuth connector registered without real token exchange. "
            "Set %s_CLIENT_SECRET to enable real OAuth. connector=%s",
            connector_name.upper(),
            server_id,
        )
    else:
        # Real token exchange would happen here (e.g. POST to the provider's
        # token endpoint with body.code + client_secret + redirect_uri).
        # For now store a clearly-marked placeholder so the shape is correct.
        auth_config = {"status": "pending_token_exchange", "grant_code": "****"}

    # In production: exchange body.code for an access token here, then store it
    # securely via the vault.  For now we register a placeholder connector so the
    # frontend flow completes end-to-end.
    reg = getattr(request.app.state, "mcp_registry", None)
    if reg is not None:
        try:
            from app.mcp.registry import MCPServerConfig

            cfg = MCPServerConfig(
                name=f"{connector_name} (OAuth)",
                url=f"https://api.{connector_name}.com",
                auth_type="bearer",
                auth_config=auth_config,
            )
            await reg.register(cfg, tenant_ctx=tenant)
        except Exception:
            _logger.debug("oauth_popup_register_skipped connector=%s", connector_name)

    return {
        "server_id": server_id,
        "name": f"{connector_name} (OAuth)",
        "status": "connected",
    }


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

    # Start the PKCE flow — generates state token + code challenge
    pkce_params = oauth_manager.start_flow(server_id=server_id, tenant_ctx=tenant_ctx)

    # Build the full authorization URL
    authorize_url = cfg.auth_config.get("authorize_url", "")
    client_id = cfg.auth_config.get("client_id", "")
    redirect_uri = (
        str(request.base_url).rstrip("/") + f"/connectors/oauth/callback?server_id={server_id}"
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
    redirect_uri: str = "",  # No hardcoded default — derived from settings or base URL
) -> dict[str, Any]:
    """OAuth callback — exchange authorization code for access tokens via PKCE."""
    # Derive redirect_uri if not provided by the caller
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


@router.get("/{connector_id}/usage")
async def get_connector_usage(
    connector_id: str,
    request: Request,
    limit: int = Query(default=20, le=100),
) -> dict:
    """Return goals that used this connector."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)

    goals = []
    total = 0
    success_count = 0

    if goal_svc is not None:
        try:
            db = getattr(goal_svc, "_db", None)
            if db:
                from sqlalchemy import text as _t

                cid_pattern = f"%{connector_id}%"
                async with db() as session:
                    await session.execute(
                        _t("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant.tenant_id},
                    )
                    rows = (
                        await session.execute(
                            _t("""
                            SELECT id, goal_text, status, created_at, cost_usd
                            FROM goals
                            WHERE tenant_id = :tid
                              AND execution_context->>'connector_ids' LIKE :cid_pattern
                            ORDER BY created_at DESC
                            LIMIT :limit
                        """),
                            {
                                "tid": tenant.tenant_id,
                                "cid_pattern": cid_pattern,
                                "limit": limit,
                            },
                        )
                    ).fetchall()
                    count_row = (
                        await session.execute(
                            _t(
                                "SELECT COUNT(*), "
                                "SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) "
                                "FROM goals "
                                "WHERE tenant_id=:tid "
                                "AND execution_context->>'connector_ids' LIKE :cid_pattern"
                            ),
                            {"tid": tenant.tenant_id, "cid_pattern": cid_pattern},
                        )
                    ).fetchone()
                    if count_row:
                        total = int(count_row[0] or 0)
                        success_count = int(count_row[1] or 0)
                    goals = [
                        {
                            "id": str(r[0]),
                            "goal": r[1],
                            "status": r[2],
                            "created_at": r[3].isoformat() if r[3] else None,
                            "cost_usd": float(r[4] or 0),
                        }
                        for r in rows
                    ]
            else:
                # In-memory fallback
                resp = await goal_svc.list_goals(tenant_ctx=tenant)
                all_goals = resp.get("goals", []) if isinstance(resp, dict) else []
                matched = [
                    g for g in all_goals if connector_id in str(g.get("execution_context", {}))
                ]
                total = len(matched)
                success_count = sum(1 for g in matched if g.get("status") == "complete")
                goals = matched[:limit]
        except Exception:
            pass

    success_rate = round(success_count / max(total, 1) * 100, 1) if total > 0 else None
    return {
        "goals": goals,
        "total": total,
        "success_rate": success_rate,
        "connector_id": connector_id,
        "filtered": True,
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
async def import_openapi_connector(request: Request, body: OpenAPIImportRequest) -> dict[str, Any]:
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
    safe_auth_type: Any = body.auth_type if body.auth_type in _valid_auth_types else "bearer"

    connector_name = body.name or (spec.get("info", {}).get("title") or "Imported API")
    connector_desc = body.description or f"Auto-imported from OpenAPI spec ({len(tools)} endpoints)"

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
        with contextlib.suppress(Exception):
            await persist_tools(tools, db, tenant_ctx.tenant_id)

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
        with contextlib.suppress(Exception):
            all_tools = await mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)

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
        tools = await mcp_client.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
    except Exception as exc:
        raise HTTPException(500, f"Discovery failed: {exc}") from exc

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
                                 :desc, CAST(:schema AS jsonb), :risk,
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
                            "schema": json.dumps(getattr(tool, "input_schema", {})),
                            "risk": getattr(tool, "risk_level", "low"),
                        },
                    )
                    saved += 1
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("tool_capability_persist_failed: %s", exc)

    return {
        "server_id": server_id,
        "tools_discovered": len(tools),
        "tools_saved": saved,
    }


@router.get("/capabilities/missing")
async def missing_capabilities(request: Request, goal: str = Query(...)) -> dict:
    """Identify capabilities needed for a goal but not yet available."""
    tenant_ctx = _require_tenant(request)
    mcp_client = getattr(request.app.state, "mcp_client", None)

    available_tools: list = []
    if mcp_client is not None:
        with contextlib.suppress(Exception):
            available_tools = await mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)

    available_names = {getattr(t, "name", "") for t in available_tools}

    suggestions: list[dict] = []
    goal_lower = goal.lower()
    for spec in CONNECTOR_CATALOG:
        name_words = spec.name.lower().replace("-", " ").replace("_", " ")
        if (
            name_words in goal_lower or spec.name.lower() in goal_lower
        ) and spec.name not in available_names:
            suggestions.append(
                {
                    "connector": spec.name,
                    "category": "integration",
                    "install_hint": (
                        f"Register the {spec.name} connector to enable this capability"
                    ),
                }
            )

    return {
        "goal": goal,
        "available_tool_count": len(available_tools),
        "missing_connectors": suggestions[:5],
        "can_proceed": len(suggestions) == 0 or len(available_tools) > 0,
    }
