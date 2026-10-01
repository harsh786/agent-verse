"""MCP HTTP client — discovers and calls tools on registered MCP servers.

Follows the MCP (Model Context Protocol) spec:
- GET /tools -> returns list of available tools
- POST /tools/{tool_name} -> executes a tool with given arguments
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.net.ssrf_guard import SSRFError, assert_public_url, public_async_client
from app.observability.logging import get_logger
from app.providers.vault import is_connector_secret_ref, resolve_connector_secret_ref
from app.tenancy.context import TenantContext

# Structlog logger: the call sites pass structured kwargs (tool=, server=, …),
# which the stdlib logging.getLogger() logger rejects with a TypeError at
# runtime. Use the project's structlog binder so those calls actually work.
logger = get_logger(__name__)
SecretResolver = Callable[..., str | None | Awaitable[str | None]]


class CircuitBreakerOpenError(Exception):
    """Raised when a circuit breaker is open and the call cannot proceed."""


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_id: str = ""
    server_name: str = ""


@dataclass
class ToolCallResult:
    tool_name: str
    success: bool
    output: Any = None
    error: str = ""
    server_id: str = ""


def _is_mcp_endpoint(url: str) -> bool:
    path = url.rstrip("/")
    return path.endswith("/mcp") or path.endswith("/mcp/authv2")


def _absolute_http_url(url: str) -> str:
    """Return an absolute HTTP(S) URL, defaulting public hostnames to HTTPS."""
    stripped = url.strip()
    if not stripped or "://" in stripped:
        return stripped
    return f"https://{stripped}"


def _ws_to_http_url(url: str) -> str:
    """Map ws:// / wss:// to http:// / https:// so the SSRF guard can check it."""
    stripped = url.strip()
    lower = stripped.lower()
    if lower.startswith("wss://"):
        return "https://" + stripped[6:]
    if lower.startswith("ws://"):
        return "http://" + stripped[5:]
    return _absolute_http_url(stripped)


# Credential keys a built-in handler uses as the vendor API endpoint.
_BUILTIN_ENDPOINT_KEYS = ("url", "base_url", "instance_url", "server_url", "endpoint")

# Database connection URIs a built-in driver dials (not HTTP): checked host by
# host (every replica-set seed / SRV target) instead of being refused on scheme.
_DSN_SCHEMES = (
    "mongodb://",
    "mongodb+srv://",
    "postgres://",
    "postgresql://",
    "mysql://",
    "redis://",
    "rediss://",
)


def _assert_egress_allowed(url: str, *, context: str) -> None:
    """Raise SSRFError unless every host ``url`` would reach is allowed."""
    if url.strip().lower().startswith(_DSN_SCHEMES):
        from app.ingestion.connector_egress import assert_source_dsn

        assert_source_dsn(url, context=context)
        return
    assert_public_url(url, context=context)


def _extract_credentials_from_server(cfg: MCPServerConfig) -> dict[str, str]:
    """Extract credentials dict from an MCPServerConfig for passing to builtin handlers.

    auth_config entries are written first. The server-level URL is added ONLY
    when auth_config does NOT already contain a 'url' or 'base_url' key.

    IMPORTANT: for builtin connectors (e.g. 'builtin-jira') whose cfg.url is set
    to a remote MCP endpoint like https://mcp.atlassian.com/v1/mcp/authv2, we must
    NOT override auth_config['url'] (which holds the actual Jira Cloud URL like
    https://pinelabsgroups.atlassian.net). Overriding it would cause jira_server.py
    to call https://mcp.atlassian.com/.../rest/api/3/search instead of the real API.
    """
    result: dict[str, str] = {}
    # auth_config entries first (connector-level credentials — these are the REAL creds)
    for key, value in (cfg.auth_config or {}).items():
        if isinstance(value, str):
            result[key] = value
    # Only add server-level URL if auth_config does not already have a url.
    # This preserves the Jira Cloud URL from auth_config when the server URL
    # is an MCP endpoint (not the actual API base URL).
    if "url" not in result and "base_url" not in result:
        for url_field in ("url", "base_url"):
            url_val = getattr(cfg, url_field, None)
            if url_val and url_val not in ("builtin://", ""):
                result["url"] = str(url_val)
                break
    return result


def _is_jira_rest_endpoint(cfg: MCPServerConfig) -> bool:
    raw_url = _absolute_http_url(cfg.url or cfg.base_url)
    if _is_mcp_endpoint(raw_url):
        return False
    url = raw_url.lower()
    name = cfg.name.lower()
    return "jira" in name or "atlassian.net" in url


def _jsonrpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


def _response_json(resp: httpx.Response, expected_id: str | None = None) -> Any:
    """Parse a JSON or SSE (text/event-stream) MCP response body.

    An MCP server speaking the streamable-HTTP transport may emit several SSE
    ``data:`` events for a single POST — e.g. `notifications/progress` or
    `notifications/message` (logging) frames — before the actual JSON-RPC
    response for the request. Those notifications are valid JSON-RPC objects
    (they have ``jsonrpc`` and ``method``) but are NOT the response: a
    JSON-RPC *response* always carries the request's ``id`` and a ``result``
    or ``error`` key, while a *notification* has no ``id`` at all.

    Blindly returning the first ``data:`` line (as this used to) silently
    treats a leading progress/log notification as the tool's result — a
    valid-looking dict with no error, so the caller reports `success=True`
    with the wrong payload instead of the real tool output.
    """
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return resp.json()

    events: list[Any] = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            with suppress(json.JSONDecodeError):
                events.append(json.loads(line[6:].strip()))
    if not events:
        return {}

    if expected_id is not None:
        for event in events:
            if isinstance(event, dict) and event.get("id") == expected_id:
                return event

    # No id to match against (or no exact match found) — prefer an actual
    # JSON-RPC response (has "result" or "error") over a notification.
    for event in events:
        if isinstance(event, dict) and ("result" in event or "error" in event):
            return event

    return events[-1]


class MCPClient:
    """HTTP client for calling tools on registered MCP servers."""

    def __init__(
        self,
        registry: MCPRegistry,
        timeout: float = 30.0,
        secret_resolver: SecretResolver | None = None,
        redis: Any = None,
        llm_provider: Any = None,
    ) -> None:
        self._registry = registry
        self._timeout = timeout
        self._secret_resolver = cast(
            "SecretResolver", secret_resolver or resolve_connector_secret_ref
        )
        self._secret_resolver_accepts_tenant = self._accepts_tenant_context(self._secret_resolver)
        # Circuit breaker support — wired externally by setting _redis
        self._circuit_breakers: dict[str, Any] = {}
        self._redis: Any = redis
        # LLM provider for self-healing tool argument repair
        self._provider: Any = llm_provider
        # OAuth manager — wired externally
        self._oauth_manager: Any = None
        # Keyed by "{tenant_id}:{server_id}", not server_id alone. server_id is a
        # per-tenant UUID for user-registered connectors (registry.py assigns a
        # fresh uuid4 per registration) so this is currently a latent risk rather
        # than a live leak — but built-in connectors ARE registered under a fixed,
        # cross-tenant-shared server_id (e.g. "builtin-github"), and only avoid
        # this path today because they always resolve a local Python handler
        # before reaching session-based HTTP dispatch. If that resolution ever
        # fails (or a future built-in ships without a handler), a bare server_id
        # key would hand tenant A's Mcp-Session-Id to tenant B's calls against
        # the same nominal server. Scoping by tenant closes that off entirely.
        self._mcp_sessions: dict[str, str] = {}
        # Per-session tool schema cache: server_id → list[ToolDefinition]
        # Avoids calling discover_tools() on every call_tool() invocation
        self._schema_cache: dict[str, list[Any]] = {}
        # Tool result cache (ToolResultCache or None) — wired externally
        self._tool_cache: Any = None

    @staticmethod
    def _accepts_tenant_context(resolver: SecretResolver) -> bool:
        try:
            signature = inspect.signature(resolver)
        except (TypeError, ValueError):
            return False
        positional_count = 0
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                return True
            if parameter.name == "tenant_ctx":
                return True
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }:
                positional_count += 1
        return positional_count >= 2

    def _http_client(self) -> httpx.AsyncClient:
        """Client for tenant connector URLs, pinned to the IPs checked at connect.

        Every caller SSRF-checks the connector URL first; a plain
        ``httpx.AsyncClient`` would resolve the name again (DNS rebinding:
        checked public IP, connected 127.0.0.1 / 169.254.169.254). Redirects are
        not followed (as before).
        """
        client: httpx.AsyncClient = public_async_client(timeout=self._timeout)
        return client

    def _get_circuit_breaker(self, server_id: str, tenant_id: str = "") -> Any:
        """Get or create a per-tenant circuit breaker for a server."""
        cb_key = f"{tenant_id}:{server_id}"
        if cb_key not in self._circuit_breakers:
            if self._redis is not None:
                try:
                    from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

                    self._circuit_breakers[cb_key] = RedisCircuitBreaker(
                        redis_client=self._redis,
                        tenant_id=tenant_id,
                        tool_name=f"mcp:{server_id}",
                        failure_threshold=5,
                        cooldown_seconds=60.0,
                    )
                except Exception:
                    return None
            else:
                from app.reliability.circuit_breaker import CircuitBreaker

                self._circuit_breakers[cb_key] = CircuitBreaker(
                    failure_threshold=5,
                    cooldown_seconds=60.0,
                )
        return self._circuit_breakers[cb_key]

    async def _resolve_auth_value(self, value: Any, tenant_ctx: TenantContext | None) -> str:
        if is_connector_secret_ref(value):
            resolved = (
                self._secret_resolver(value, tenant_ctx)
                if self._secret_resolver_accepts_tenant
                else self._secret_resolver(value)
            )
            if inspect.isawaitable(resolved):
                resolved = await resolved
            return resolved or ""
        return str(value)

    async def _ensure_mcp_session(
        self,
        client: httpx.AsyncClient,
        cfg: MCPServerConfig,
        headers: dict[str, str],
        tenant_id: str = "",
    ) -> dict[str, str]:
        # Scoped by (tenant_id, server_id): server_id alone is not guaranteed
        # tenant-unique (built-in connectors share a fixed canonical id across
        # every tenant that registers them) — see the comment on
        # ``self._mcp_sessions`` in __init__.
        cache_key = f"{tenant_id}:{cfg.server_id}"
        session = self._mcp_sessions.get(cache_key)
        if session:
            return {**headers, "Mcp-Session-Id": session}

        init_resp = await client.post(
            cfg.url.rstrip("/"),
            json=_jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "agentverse", "version": "0.1.0"},
                },
            ),
            headers=headers,
        )
        init_resp.raise_for_status()
        session = init_resp.headers.get("mcp-session-id")
        if not session:
            return headers
        self._mcp_sessions[cache_key] = session
        session_headers = {**headers, "Mcp-Session-Id": session}
        await client.post(
            cfg.url.rstrip("/"),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=session_headers,
        )
        return session_headers

    @staticmethod
    def _requires_initialize(resp: httpx.Response) -> bool:
        if resp.status_code not in {400, 401}:
            return False
        return "initialize" in resp.text.lower()

    async def discover_tools(
        self, *, server_id: str, tenant_ctx: TenantContext
    ) -> list[ToolDefinition]:
        """Discover available tools on a registered MCP server."""
        from opentelemetry import trace as _trace

        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("mcp.discover_tools") as span:
            span.set_attribute("server_id", server_id)
            span.set_attribute("tenant_id", getattr(tenant_ctx, "tenant_id", ""))
        if not server_id:
            logger.warning("discover_tools called with empty server_id")
            return []
        cfg = await self._registry.get(server_id, tenant_ctx=tenant_ctx)
        if cfg is None:
            return []

        # ── Builtin servers MUST be checked first ─────────────────────────────
        # The builtin_handler is a Python callable that is NOT serialised to Redis.
        # After a Redis round-trip it will be None, but the URL prefix "builtin://"
        # identifies these servers unambiguously.
        #
        # EXTENDED: also check process-local handler registry even when the URL
        # is NOT "builtin://" — this handles connectors like "builtin-jira" whose
        # URL was set to https://mcp.atlassian.com/... by the user but which have
        # a registered native Python handler.  Without this check, discover_tools
        # falls through to HTTP discovery → gets Atlassian tool names ("search_issues"
        # instead of "jira_search_issues") → tool name mismatch → 401 errors.
        is_builtin = (
            cfg.builtin_handler is not None
            or (cfg.base_url or "").startswith("builtin://")
            or (cfg.url or "").startswith("builtin://")
        )

        # Try to restore builtin handler from process-local registry regardless of URL.
        # IMPORTANT: cfg.server_id may be a UUID (the key under which the user's
        # registration is stored) while the builtin handler is registered under the
        # canonical builtin server_id (e.g. 'builtin-jira').  Try both.
        if cfg.builtin_handler is None:
            try:
                from app.mcp.registry import MCPRegistry as _MCPReg

                _restored = (
                    # By built-in TYPE first: several connections share one type.
                    (_MCPReg.get_builtin_handler(cfg.builtin_type) if cfg.builtin_type else None)
                    or _MCPReg.get_builtin_handler(server_id)  # e.g. 'builtin-jira'
                    or _MCPReg.get_builtin_handler(cfg.server_id)  # UUID fallback
                )
                if _restored is not None:
                    cfg = cfg.model_copy(update={"builtin_handler": _restored})
                    is_builtin = True  # treat as builtin now that we have the handler
            except Exception:
                pass

        if is_builtin and cfg.tool_definitions:
            return [
                ToolDefinition(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    input_schema=t.get(
                        "parameters", t.get("inputSchema", t.get("input_schema", {}))
                    ),
                    server_id=server_id,
                    server_name=cfg.name,
                )
                for t in cfg.tool_definitions
                if t.get("name")
            ]

        if is_builtin and not cfg.tool_definitions:
            # Handler found but no tool defs in the DB/Redis config.
            # Fetch tool definitions directly from the builtin handler module so
            # the planner gets a complete tool list (not an empty one).
            try:
                from app.mcp.servers import registry_wiring as _rw

                for _bcfg in _rw.get_builtin_server_configs():
                    if _bcfg.get("server_id") in (server_id, cfg.builtin_type):
                        _tool_defs = _bcfg.get("tool_definitions", [])
                        return [
                            ToolDefinition(
                                name=str(t.get("name", "")),
                                description=str(t.get("description", "")),
                                input_schema=t.get(
                                    "parameters", t.get("inputSchema", t.get("input_schema", {}))
                                ),
                                server_id=server_id,
                                server_name=cfg.name,
                            )
                            for t in _tool_defs
                            if t.get("name")
                        ]
            except Exception:
                pass
            return []  # fallback: no tools known for this builtin

        # ── OpenAPI-imported connector ────────────────────────────────────────
        # Its tools are the operations extracted from the spec and stored on the
        # config (dispatched by _dispatch_openapi_tool). The target is a plain
        # REST API, not an MCP server, so HTTP ``tools/list`` discovery would
        # find nothing and the imported tools were invisible to the planner.
        _openapi_defs = [
            t
            for t in (cfg.tool_definitions or [])
            if isinstance(t, dict) and t.get("name") and t.get("http_method")
        ]
        if _openapi_defs:
            return [
                ToolDefinition(
                    name=str(t["name"]),
                    description=str(t.get("description", "")),
                    input_schema=dict(t.get("parameters") or t.get("input_schema") or {}),
                    server_id=server_id,
                    server_name=cfg.name,
                )
                for t in _openapi_defs
            ]

        # ── Non-builtin Jira REST connector ───────────────────────────────────
        # A user-registered Jira connector (e.g. the "PineLabs JIRA" record)
        # exposes a synthetic jira_search_issues tool via the Jira REST API.
        if _is_jira_rest_endpoint(cfg):
            return [
                ToolDefinition(
                    name="jira_search_issues",
                    description="Search Jira issues using JQL (Jira Query Language)",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "jql": {"type": "string"},
                            "max_results": {"type": "integer", "default": 50},
                            "start_at": {"type": "integer", "default": 0},
                            "fields": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["jql"],
                    },
                    server_id=server_id,
                    server_name=cfg.name,
                )
            ]

        headers = await self._build_auth_headers(cfg, tenant_ctx=tenant_ctx, server_id=server_id)
        is_mcp_endpoint = _is_mcp_endpoint(cfg.url)
        if is_mcp_endpoint:
            headers["Accept"] = "application/json, text/event-stream"
            headers["Content-Type"] = "application/json"

        # SSRF guard — validate URL before any outbound HTTP call
        _disc_url = _absolute_http_url(cfg.url or cfg.base_url or "")
        if _disc_url and not _disc_url.startswith("builtin://"):
            try:
                assert_public_url(_disc_url, context=f"MCP discover_tools {server_id}")
            except SSRFError as exc:
                logger.warning(
                    "ssrf_guard_blocked_discover: server_id=%s, error=%s", server_id, str(exc)
                )
                raise ValueError(f"Connector URL blocked by SSRF guard: {exc}") from exc

        try:
            async with self._http_client() as client:
                _list_req_id: str | None = None
                if is_mcp_endpoint:
                    _list_req = _jsonrpc("tools/list")
                    _list_req_id = _list_req["id"]
                    resp = await client.post(
                        cfg.url.rstrip("/"),
                        json=_list_req,
                        headers=headers,
                    )
                    if self._requires_initialize(resp):
                        headers = await self._ensure_mcp_session(
                            client, cfg, headers, tenant_id=getattr(tenant_ctx, "tenant_id", "")
                        )
                        _list_req = _jsonrpc("tools/list")
                        _list_req_id = _list_req["id"]
                        resp = await client.post(
                            cfg.url.rstrip("/"),
                            json=_list_req,
                            headers=headers,
                        )
                else:
                    resp = await client.get(f"{cfg.url.rstrip('/')}/tools", headers=headers)
                resp.raise_for_status()
                data = _response_json(resp, expected_id=_list_req_id)
                if isinstance(data, list):
                    tools = data
                elif isinstance(data, dict):
                    result = data.get("result")
                    if isinstance(result, dict):
                        tools = result.get("tools", [])
                    else:
                        tools = data.get("tools", [])
                else:
                    tools = []
                if not isinstance(tools, list):
                    return []
                return [
                    ToolDefinition(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", t.get("input_schema", {})),
                        server_id=server_id,
                        server_name=cfg.name,
                    )
                    for t in tools
                    if t.get("name")
                ]
        except Exception:
            return []

    async def discover_all_tools(self, *, tenant_ctx: TenantContext) -> list[ToolDefinition]:
        """Discover all tools across all registered servers for this tenant."""
        all_tools: list[ToolDefinition] = []
        try:
            # Use public API instead of accessing private _redis directly.
            records = await self._registry.list_server_records(tenant_ctx=tenant_ctx)
            for sid_str, _ in records:
                tools = await self.discover_tools(server_id=sid_str, tenant_ctx=tenant_ctx)
                all_tools.extend(tools)
        except Exception as exc:
            logger.warning("discover_all_tools failed: %s", exc)
        return all_tools

    async def _dispatch_builtin_tool(
        self,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext | None = None,
    ) -> ToolCallResult:
        """Call a built-in server's Python handler directly."""
        handler = server.builtin_handler
        if handler is None:
            # The handler is a Python callable that cannot survive Redis serialisation.
            # Try to restore it from the process-local registry populated by
            # MCPRegistry.register_builtin_handler() — called at startup in the web
            # process and at context-build time in Celery workers (Fix 2).
            try:
                from app.mcp.registry import MCPRegistry as _MCPReg

                handler = (
                    _MCPReg.get_builtin_handler(server.builtin_type)
                    if server.builtin_type
                    else None
                ) or _MCPReg.get_builtin_handler(server.server_id)
            except Exception:
                pass
        if handler is None:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error="Built-in handler not available (lost after Redis round-trip)",
                server_id=server.server_id,
            )
        credentials = _extract_credentials_from_server(server)

        # Resolve vault secret references so builtin handlers receive plain-text
        # credentials instead of "vault://connectors/..." or "secret://..." vault refs.
        # Without this, jira_server.py receives the vault ref as the API token → 401.
        # Use module-level is_connector_secret_ref (already imported at top of file).
        _has_secret_ref = any(
            isinstance(v, str) and (v.startswith("secret://") or is_connector_secret_ref(v))
            for v in credentials.values()
        )
        if _has_secret_ref:
            # Fail closed. This used to swallow every resolver error and pass the
            # raw "vault://…" reference to the handler as if it were the secret
            # (so the vendor API got the reference string as a password, and a
            # handler with an env fallback silently ran on platform credentials).
            # It also consulted the process-global secret mapping *before* the
            # tenant-aware resolver; canonical ids such as ``builtin-jira`` are
            # shared by every tenant, so that could return another tenant's secret.
            resolved: dict[str, str] = {}
            for k, v in credentials.items():
                # Check for BOTH vault://connectors/ and secret://connector/ formats
                if not (
                    isinstance(v, str) and (v.startswith("secret://") or is_connector_secret_ref(v))
                ):
                    resolved[k] = v
                    continue
                plain: Any = None
                try:
                    if self._secret_resolver_accepts_tenant and tenant_ctx is not None:
                        plain = self._secret_resolver(v, tenant_ctx)
                    else:
                        plain = self._secret_resolver(v)
                    # The resolver may be sync (the default resolve_connector_secret_ref)
                    # or async — only await when it returned an awaitable.
                    if inspect.isawaitable(plain):
                        plain = await plain
                except Exception as exc:
                    logger.warning(
                        "builtin_secret_resolve_failed server_id=%s key=%s error=%s",
                        server.server_id,
                        k,
                        str(exc)[:120],
                    )
                    plain = None
                if not plain:
                    return ToolCallResult(
                        tool_name=tool_name,
                        success=False,
                        error=(
                            f"Could not resolve the credential '{k}' for connector "
                            f"'{server.name}'; re-enter the connector's credentials."
                        ),
                        server_id=server.server_id,
                    )
                resolved[k] = str(plain)
            credentials = resolved
        # Confused-deputy guard (defence in depth): built-in handlers read their
        # configuration via tenant_getenv(), which answers only from these
        # credentials during the call (app/mcp/servers/credentials.py), and a
        # connector with no credentials at all is refused before the handler runs.
        from app.mcp.servers.registry_wiring import (
            builtin_required_env,
            has_tenant_credentials,
        )

        # SSRF guard for the endpoint the handler will call. Built-in handlers
        # (jira_server, github_server, ...) use the tenant's connector
        # auth_config['url'/'base_url'] as their API base with no check, so a
        # tenant could point one at http://169.254.169.254 or an internal host.
        for _ep_key in _BUILTIN_ENDPOINT_KEYS:
            _ep = credentials.get(_ep_key)
            if not isinstance(_ep, str) or not _ep.strip() or _ep.startswith("builtin://"):
                continue
            try:
                _assert_egress_allowed(
                    _absolute_http_url(_ep), context=f"MCP built-in {server.server_id}"
                )
            except SSRFError as exc:
                logger.warning(
                    "ssrf_guard_blocked_builtin: server_id=%s key=%s error=%s",
                    server.server_id,
                    _ep_key,
                    str(exc),
                )
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    error="Connector URL blocked by SSRF guard",
                    server_id=server.server_id,
                )

        _required_env = builtin_required_env(server.builtin_type or server.server_id, server.name)
        if _required_env and not has_tenant_credentials(credentials, _required_env):
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=(
                    f"Connector '{server.name}' has no credentials configured for this "
                    "tenant. Configure credentials on the connector; platform "
                    "credentials are never used for tenant tool calls."
                ),
                server_id=server.server_id,
            )
        try:
            # Detect credentials support once via signature to avoid double-invocation
            # and to prevent masking TypeErrors raised inside the handler body.
            try:
                sig = inspect.signature(handler)
                accepts_credentials = "credentials" in sig.parameters
            except (ValueError, TypeError):
                accepts_credentials = False

            if accepts_credentials:
                output = await handler(tool_name, arguments, credentials=credentials)
            else:
                output = await handler(tool_name, arguments)
        except Exception as exc:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                server_id=server.server_id,
            )
        if isinstance(output, dict) and output.get("error"):
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(output["error"]),
                output=output,
                server_id=server.server_id,
            )
        return ToolCallResult(
            tool_name=tool_name,
            success=True,
            output=output,
            server_id=server.server_id,
        )

    async def _dispatch_openapi_tool(
        self,
        server: MCPServerConfig,
        tool_def: dict[str, Any],
        arguments: dict[str, Any],
        tenant_ctx: TenantContext | None = None,
    ) -> ToolCallResult:
        """Dispatch an HTTP call for an OpenAPI-imported tool definition.

        Bug fixed: this used to send only ``Content-Type`` — the connector's
        configured credentials were never attached, so every authenticated API
        answered 401. It now applies the connector's auth (bearer / API key /
        basic / custom header / OAuth, via ``_build_auth_headers``), honouring an
        API key's spec placement (``auth_config["in"]``: header | query | cookie).
        Secrets are scrubbed from any returned error, and the SSRF guard runs on
        the FINAL request URL (the caller's check used ``url or base_url`` while
        this used ``base_url or url``, and the registry-miss fallback had none).
        """
        from urllib.parse import quote

        effective_base = server.base_url or server.url
        http_method = tool_def.get("http_method", "POST").upper()
        http_path = str(tool_def.get("http_path", ""))
        tool_name = tool_def.get("name") or tool_def.get("tool_name", "")

        # Substitute OpenAPI path parameters ({item_id}) and drop them from the
        # query/body — they were previously sent literally as "{item_id}".
        args = dict(arguments)
        for key in list(args):
            placeholder = "{" + key + "}"
            if placeholder in http_path:
                http_path = http_path.replace(placeholder, quote(str(args.pop(key)), safe=""))
        url = effective_base.rstrip("/") + "/" + http_path.lstrip("/")

        try:
            assert_public_url(url, context=f"OpenAPI tool {tool_name}")
        except SSRFError as exc:
            logger.warning("ssrf_guard_blocked_openapi: server_id=%s", server.server_id)
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"Target URL blocked by SSRF guard: {exc}",
                server_id=server.server_id,
            )

        try:
            auth_headers = await self._build_auth_headers(
                server, tenant_ctx=tenant_ctx, server_id=server.server_id
            )
        except Exception:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error="Could not resolve the connector's credentials",
                server_id=server.server_id,
            )
        auth_params: dict[str, str] = {}
        auth_cfg = server.auth_config or {}
        placement = str(auth_cfg.get("in", "header")).lower()
        if server.auth_type == "api_key" and placement in {"query", "cookie"}:
            header_name = str(auth_cfg.get("header_name", "X-API-Key"))
            param_name = str(auth_cfg.get("param_name") or header_name)
            key_value = auth_headers.pop(header_name, "")
            if key_value and placement == "query":
                auth_params[param_name] = key_value
            elif key_value:
                auth_headers["Cookie"] = f"{param_name}={key_value}"
        secrets = [v for v in (*auth_headers.values(), *auth_params.values()) if v]
        secrets += [s.split(" ", 1)[1] for s in secrets if " " in s]  # "Bearer <tok>"

        def _scrub(text: str) -> str:
            for secret in secrets:
                text = text.replace(secret, "***")
            return text

        headers = {"Content-Type": "application/json", **auth_headers}

        try:
            async with self._http_client() as client:
                if http_method == "GET":
                    resp = await client.get(url, params={**args, **auth_params}, headers=headers)
                else:
                    resp = await client.request(
                        http_method,
                        url,
                        params=auth_params or None,
                        json=args,
                        headers=headers,
                    )
                resp.raise_for_status()
                body = resp.json()
                # H1 Fix: HTTP 200 with {"error": "..."} body must be treated as failure
                if isinstance(body, dict) and body.get("error"):
                    return ToolCallResult(
                        tool_name=tool_name,
                        success=False,
                        error=str(body["error"]),
                        output=body,
                        server_id=server.server_id,
                    )
                return ToolCallResult(
                    tool_name=tool_name,
                    success=True,
                    output=body,
                    server_id=server.server_id,
                )
        except httpx.HTTPStatusError as exc:
            # Built from the response, not str(exc): that message embeds the
            # request URL — including a query-string API key.
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=_scrub(f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"),
                server_id=server.server_id,
            )
        except Exception as exc:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=_scrub(str(exc)),
                server_id=server.server_id,
            )

    async def _dispatch_jira_rest_tool(
        self,
        server: MCPServerConfig,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> ToolCallResult:
        if tool_name != "jira_search_issues":
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error="Unsupported Jira REST tool",
                server_id=server_id,
            )

        headers = await self._build_auth_headers(server, tenant_ctx=tenant_ctx, server_id=server_id)
        default_fields = [
            "summary",
            "status",
            "assignee",
            "priority",
            "created",
            "updated",
            "issuetype",
        ]
        payload: dict[str, Any] = {
            "jql": arguments["jql"],
            "maxResults": arguments.get("max_results", 50),
            "fields": arguments.get("fields", default_fields),
        }
        if arguments.get("next_page_token"):
            payload["nextPageToken"] = arguments["next_page_token"]

        try:
            base_url = _absolute_http_url(server.url or server.base_url).rstrip("/")
            async with self._http_client() as client:
                resp = await client.post(
                    f"{base_url}/rest/api/3/search/jql",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            issues = data.get("issues", [])
            return ToolCallResult(
                tool_name=tool_name,
                success=True,
                output={
                    "total": data.get("total", len(issues)),
                    "start_at": data.get("startAt", 0),
                    "max_results": data.get("maxResults", 50),
                    "issues": [
                        {
                            "id": issue.get("id", ""),
                            "key": issue.get("key", ""),
                            "summary": (issue.get("fields") or {}).get("summary", ""),
                            "status": ((issue.get("fields") or {}).get("status") or {}).get(
                                "name", ""
                            ),
                            "priority": ((issue.get("fields") or {}).get("priority") or {}).get(
                                "name", ""
                            ),
                            "assignee": ((issue.get("fields") or {}).get("assignee") or {}).get(
                                "displayName", ""
                            ),
                            "issue_type": ((issue.get("fields") or {}).get("issuetype") or {}).get(
                                "name", ""
                            ),
                            "created": (issue.get("fields") or {}).get("created", ""),
                            "updated": (issue.get("fields") or {}).get("updated", ""),
                        }
                        for issue in issues
                    ],
                },
                server_id=server_id,
            )
        except Exception as exc:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                server_id=server_id,
            )

    async def _call_tool_impl(
        self,
        cfg: MCPServerConfig,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> ToolCallResult:
        """Inner dispatch logic — raises on any error for circuit-breaker accounting."""
        # 1. Built-in server (Python handler)
        #
        # ALWAYS try to restore the builtin handler before dispatch.
        # The handler is a Python callable that is NOT serialised to Redis.
        # After a Redis round-trip, cfg.builtin_handler is None even for
        # connectors like 'builtin-jira' whose URL is set to the Atlassian
        # remote MCP (https://mcp.atlassian.com/...).  Without restoration,
        # the client falls through to HTTP dispatch against that remote URL,
        # which requires OAuth — not the Basic auth stored in auth_config.
        if cfg.builtin_handler is None:
            try:
                from app.mcp.registry import MCPRegistry as _MCPReg

                # The config's server_id may be a UUID (the key under which the user's
                # registration is stored) while the builtin handler is registered under
                # the canonical builtin server_id (e.g. 'builtin-jira').
                # Try cfg.server_id first, then fall back to the original server_id arg.
                _restored = (
                    (_MCPReg.get_builtin_handler(cfg.builtin_type) if cfg.builtin_type else None)
                    or _MCPReg.get_builtin_handler(cfg.server_id)
                    or _MCPReg.get_builtin_handler(server_id)
                )
                # The process-local handler registry is only populated when THIS
                # process registered the connector (or wired it at startup with a
                # valid env key). A Celery worker that runs the goal has neither,
                # so fall back to the module-level built-in configs — matching by
                # canonical server_id or connector name — and cache the result.
                if _restored is None:
                    try:
                        from app.mcp.servers.registry_wiring import (
                            get_builtin_server_configs as _gbsc,
                        )

                        _name = (cfg.name or "").strip().lower()
                        for _bcfg in _gbsc():
                            if _bcfg.get("server_id") in (
                                cfg.builtin_type,
                                cfg.server_id,
                                server_id,
                            ) or (
                                _name and _bcfg.get("name", "").strip().lower() == _name
                            ):
                                _restored = _bcfg.get("handler")
                                break
                    except Exception:
                        pass
                if _restored is not None:
                    cfg = cfg.model_copy(update={"builtin_handler": _restored})
                    import contextlib as _contextlib

                    with _contextlib.suppress(Exception):
                        _MCPReg.register_builtin_handler(cfg.server_id, _restored)
                    logger.info(
                        "builtin_handler_restored",
                        cfg_server_id=cfg.server_id,
                        lookup_server_id=server_id,
                        tool=tool_name,
                    )
                else:
                    logger.warning(
                        "builtin_handler_not_found",
                        cfg_server_id=cfg.server_id,
                        lookup_server_id=server_id,
                        tool=tool_name,
                        url=cfg.url,
                    )
            except Exception as _bh_exc:
                logger.warning("builtin_handler_restore_error: %s", _bh_exc)

        # SSRF guard — validate the server URL (and WS URL) before ANY dispatch.
        # It used to run only after the built-in and WebSocket branches had
        # already returned, so a built-in connector (whose handler calls the
        # connector's tenant-supplied URL) or a ws:// connector reached internal
        # addresses unchecked.
        _transport = cfg.transport or "http"
        _guard_urls: list[str] = []
        _request_url = _absolute_http_url(cfg.url or cfg.base_url or "")
        if _request_url and not _request_url.startswith("builtin://"):
            _guard_urls.append(_request_url)
        if _transport in ("ws", "websocket") and cfg.ws_url:
            _guard_urls.append(_ws_to_http_url(cfg.ws_url))
        for _guard_url in _guard_urls:
            try:
                _assert_egress_allowed(_guard_url, context=f"MCP server {server_id}")
            except SSRFError as exc:
                logger.warning(
                    "ssrf_guard_blocked_mcp: server_id=%s, error=%s", server_id, str(exc)
                )
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    error="Server URL blocked by SSRF guard",
                    server_id=server_id,
                )

        if cfg.builtin_handler is not None:
            return await self._dispatch_builtin_tool(cfg, tool_name, arguments, tenant_ctx)

        # 1.5. WebSocket transport — route to MCPWebSocketClient when transport="ws"/"websocket"
        if _transport in ("ws", "websocket") and cfg.ws_url:
            try:
                from app.mcp.ws_client import MCPWebSocketClient

                async with MCPWebSocketClient(ws_url=cfg.ws_url) as _ws_client:
                    _ws_result = await _ws_client.call_tool(
                        tool_name=tool_name, arguments=arguments
                    )
                return ToolCallResult(
                    tool_name=tool_name,
                    success=True,
                    output=_ws_result,
                    server_id=server_id,
                )
            except Exception as _ws_exc:
                logger.warning(
                    "ws_mcp_call_failed tool=%s error=%s",
                    tool_name,
                    str(_ws_exc)[:80],
                )
                # Fall through to HTTP dispatch

        # 2. OpenAPI-imported tool stored in tool_definitions
        if cfg.tool_definitions:
            for tdef in cfg.tool_definitions:
                if tdef.get("name") == tool_name or tdef.get("tool_name") == tool_name:
                    return await self._dispatch_openapi_tool(cfg, tdef, arguments, tenant_ctx)

        # 3. Jira REST connector registered with an Atlassian base URL
        if _is_jira_rest_endpoint(cfg):
            return await self._dispatch_jira_rest_tool(
                cfg, server_id, tool_name, arguments, tenant_ctx
            )

        # 4. Normal MCP/HTTP dispatch
        headers = await self._build_auth_headers(cfg, tenant_ctx=tenant_ctx, server_id=server_id)
        headers["Content-Type"] = "application/json"
        using_jsonrpc = _is_mcp_endpoint(cfg.url)
        if using_jsonrpc:
            headers["Accept"] = "application/json, text/event-stream"

        async with self._http_client() as client:
            _call_req_id: str | None = None
            if using_jsonrpc:
                _call_req = _jsonrpc(
                    "tools/call",
                    {"name": tool_name, "arguments": arguments},
                )
                _call_req_id = _call_req["id"]
                resp = await client.post(
                    cfg.url.rstrip("/"),
                    json=_call_req,
                    headers=headers,
                )
                if self._requires_initialize(resp):
                    headers = await self._ensure_mcp_session(
                        client, cfg, headers, tenant_id=getattr(tenant_ctx, "tenant_id", "")
                    )
                    _call_req = _jsonrpc(
                        "tools/call",
                        {"name": tool_name, "arguments": arguments},
                    )
                    _call_req_id = _call_req["id"]
                    resp = await client.post(
                        cfg.url.rstrip("/"),
                        json=_call_req,
                        headers=headers,
                    )
            else:
                resp = await client.post(
                    f"{cfg.url.rstrip('/')}/tools/{tool_name}",
                    json={"arguments": arguments},
                    headers=headers,
                )
            resp.raise_for_status()
            payload = _response_json(resp, expected_id=_call_req_id)
            is_jsonrpc_response = using_jsonrpc or (
                isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"
            )
            if is_jsonrpc_response and isinstance(payload, dict) and "error" in payload:
                error = payload["error"]
                if isinstance(error, dict):
                    code = error.get("code")
                    message = error.get("message", str(error))
                    error_text = f"JSON-RPC error {code}: {message}"
                else:
                    error_text = f"JSON-RPC error: {error}"
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    error=error_text,
                    server_id=server_id,
                )
            if is_jsonrpc_response and isinstance(payload, dict):
                output = payload.get("result", payload)
            else:
                output = payload
            if isinstance(output, dict) and output.get("isError") is True:
                content = output.get("content", [])
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict):
                        error_text = str(first.get("text", output))
                    else:
                        error_text = str(first)
                else:
                    error_text = str(output)
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    error=error_text,
                    server_id=server_id,
                )
            return ToolCallResult(
                tool_name=tool_name,
                success=True,
                output=output,
                server_id=server_id,
            )

    async def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> ToolCallResult:
        """Execute a tool on an MCP server.

        Raises:
            CircuitBreakerOpenError: If the circuit breaker for this server is open.
        """
        logger.info("call_tool_entry server_id=%s tool=%s", server_id, tool_name)
        import time as _time

        # Input validation
        if not server_id:
            return ToolCallResult(
                tool_name=tool_name or "",
                success=False,
                error="server_id must not be empty",
            )
        if not tool_name:
            return ToolCallResult(
                tool_name="",
                success=False,
                error="tool_name must not be empty",
                server_id=server_id,
            )
        if not isinstance(arguments, dict):
            arguments = {}

        # Circuit breaker check — raises CircuitBreakerOpenError if open
        cb = self._get_circuit_breaker(server_id, tenant_id=getattr(tenant_ctx, "tenant_id", ""))
        if cb is not None:
            try:
                if not await cb.can_call_async():
                    # Try stale cache fallback before raising
                    _tc = getattr(self, "_tool_cache", None)
                    if _tc is not None:
                        try:
                            _stale = await _tc.get_stale(
                                server_id=server_id,
                                tool_name=tool_name,
                                arguments=arguments,
                                tenant_id=getattr(tenant_ctx, "tenant_id", ""),
                            )
                            if _stale is not None:
                                logger.info(
                                    "circuit_breaker_stale_cache_served",
                                    server=server_id,
                                    tool=tool_name,
                                )
                                return ToolCallResult(
                                    tool_name=tool_name,
                                    success=True,
                                    output=_stale,
                                    server_id=server_id,
                                )
                        except Exception:
                            pass
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker open for {server_id}. Retrying after cooldown."
                    )
            except CircuitBreakerOpenError:
                raise
            except Exception:
                pass  # CB check failure must never block tool calls

        cfg = await self._registry.get(server_id, tenant_ctx=tenant_ctx)
        if cfg is None:
            # Fallback: scan all registered servers for an OpenAPI-imported tool
            try:
                for server in await self._registry.list_all(tenant_ctx=tenant_ctx):
                    if server.tool_definitions:
                        for tdef in server.tool_definitions:
                            if tdef.get("name") == tool_name or tdef.get("tool_name") == tool_name:
                                return await self._dispatch_openapi_tool(
                                    server=server,
                                    tool_def=tdef,
                                    arguments=arguments,
                                    tenant_ctx=tenant_ctx,
                                )
            except Exception:
                pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"Server {server_id} not found",
            )

        # A tool exposed by several connections is offered as
        # "<connection>__<tool>" (app/mcp/tool_naming.py); this call is already
        # routed to its connection, so dispatch the real tool name.
        from app.mcp.tool_naming import strip_connection_prefix

        tool_name = strip_connection_prefix(tool_name, cfg.name)

        _tenant_id = getattr(tenant_ctx, "tenant_id", "")
        _t0 = _time.monotonic()

        # ── Universal Intelligence Layer ───────────────────────────────────────
        # Step 1: Resolve arguments against the tool's JSON schema before the
        # first call so the LLM's parameter name variations are fixed upstream.
        try:
            from app.mcp.tool_intelligence import get_healer, get_resolver

            _resolver = get_resolver()
            _healer = get_healer(getattr(self, "_provider", None))

            # Get the tool schema — try multiple sources:
            # Source A: cfg.tool_definitions (builtin + OpenAPI servers)
            # Source B: live discover_tools() call (external MCP servers)
            _tool_schema: dict | None = None
            for _tdef in cfg.tool_definitions or []:
                if _tdef.get("name") == tool_name:
                    _tool_schema = (
                        _tdef.get("parameters")
                        or _tdef.get("inputSchema")
                        or _tdef.get("input_schema")
                        or {}
                    )
                    break

            # Source B: If not found in stored definitions, use per-session schema cache.
            # Only do live discover_tools() for non-MCP-endpoint servers (e.g. REST APIs)
            # to avoid extra network round-trips for JSON-RPC MCP endpoints which handle
            # tool listing separately from tool calling.
            if not _tool_schema and not _is_mcp_endpoint(
                getattr(cfg, "url", "") or getattr(cfg, "base_url", "") or ""
            ):
                try:
                    # Check per-session schema cache first
                    _cache_key = f"{server_id}:{tenant_ctx.tenant_id}"
                    if _cache_key not in self._schema_cache:
                        _live_tools = await self.discover_tools(
                            server_id=server_id, tenant_ctx=tenant_ctx
                        )
                        self._schema_cache[_cache_key] = _live_tools
                    else:
                        _live_tools = self._schema_cache[_cache_key]
                    for _lt in _live_tools:
                        if _lt.name == tool_name:
                            _tool_schema = _lt.input_schema or {}
                            break
                except Exception:
                    pass

            # Normalise arguments against schema (zero-cost, no LLM)
            if _tool_schema:
                arguments = _resolver.resolve(_tool_schema, arguments)
        except Exception as _ti_exc:
            logger.debug("tool_intelligence_resolve_skipped: %s", _ti_exc)

        # ── Tool Result Cache: return immediately on cache hit ─────────────────
        _tc = getattr(self, "_tool_cache", None)
        if _tc is not None:
            try:
                _cached_result = await _tc.get(
                    server_id=server_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    tenant_id=_tenant_id,
                )
                if _cached_result is not None:
                    return ToolCallResult(
                        tool_name=tool_name,
                        success=True,
                        output=_cached_result,
                        server_id=server_id,
                    )
            except Exception:
                pass

        # Exfiltration guard for write tools
        try:
            from app.agent.exfil_guard import check_tool_args_for_exfil

            _blocked, _reason = check_tool_args_for_exfil(
                tool_name, arguments, tenant_id=_tenant_id
            )
            if _blocked:
                logger.warning("exfil_guard_blocked: tool=%s, reason=%s", tool_name, _reason[:100])
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Tool call blocked by data exfiltration guard: {_reason}",
                    server_id=server_id,
                )
        except Exception as _exfil_exc:
            # Fail closed: an error in the exfiltration guard used to let the
            # (possibly exfiltrating) write call through unchecked.
            logger.warning(
                "exfil_guard_error_blocking: tool=%s, error=%s", tool_name, str(_exfil_exc)[:100]
            )
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error="Tool call blocked: the data exfiltration guard could not evaluate it",
                server_id=server_id,
            )

        try:
            result = await self._call_tool_impl(cfg, server_id, tool_name, arguments, tenant_ctx)

            # Store successful result in tool cache (with stale backup)
            if result.success and _tc is not None:
                try:
                    from app.mcp.tool_cache import classify_tool

                    if classify_tool(tool_name) == "write":
                        # Write tool succeeded — invalidate cached reads for this server
                        await _tc.invalidate_writes(
                            tool_name=tool_name,
                            tenant_id=_tenant_id,
                            server_id=server_id,
                        )
                    else:
                        await _tc.set_with_stale(
                            server_id=server_id,
                            tool_name=tool_name,
                            arguments=arguments,
                            result=result.output,
                            tenant_id=_tenant_id,
                            duration_ms=(_time.monotonic() - _t0) * 1000,
                        )
                except Exception:
                    pass

            # ── Self-healing: retry if argument error ──────────────────────────
            if (
                not result.success and _healer.is_argument_error(result.error)  # type: ignore[union-attr]
            ):
                try:
                    logger.info(
                        "self_heal_triggered",
                        tool=tool_name,
                        error=str(result.error)[:100],
                    )
                    _healed_args = await _healer.heal(  # type: ignore[union-attr]
                        tool_name=tool_name,
                        tool_schema=_tool_schema,  # type: ignore[name-defined]
                        original_arguments=arguments,
                        failed_result=result,
                        resolver=_resolver,  # type: ignore[name-defined]
                        tenant_ctx=tenant_ctx,
                    )
                    if _healed_args != arguments:
                        result = await self._call_tool_impl(
                            cfg, server_id, tool_name, _healed_args, tenant_ctx
                        )
                        if result.success:
                            logger.info("self_heal_succeeded: tool=%s", tool_name)
                except Exception as _heal_exc:
                    logger.warning("self_heal_error: %s", _heal_exc)
            _latency_ms = (_time.monotonic() - _t0) * 1000
            if cb is not None:
                with suppress(Exception):
                    await cb.record_success_async()
            # Update tool capability stats
            try:
                _db = getattr(self, "_db", None)
                await self._update_tool_stats(
                    server_id,
                    tool_name,
                    _tenant_id,
                    success=result.success,
                    latency_ms=_latency_ms,
                    db=_db,
                )
            except Exception:
                pass
            return result
        except CircuitBreakerOpenError:
            raise
        except httpx.HTTPStatusError as exc:
            _latency_ms = (_time.monotonic() - _t0) * 1000
            if cb is not None:
                with suppress(Exception):
                    await cb.record_failure_async()
            try:
                _db = getattr(self, "_db", None)
                await self._update_tool_stats(
                    server_id, tool_name, _tenant_id, success=False, latency_ms=_latency_ms, db=_db
                )
            except Exception:
                pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                server_id=server_id,
            )
        except Exception as exc:
            _latency_ms = (_time.monotonic() - _t0) * 1000
            if cb is not None:
                with suppress(Exception):
                    await cb.record_failure_async()
            try:
                _db = getattr(self, "_db", None)
                await self._update_tool_stats(
                    server_id, tool_name, _tenant_id, success=False, latency_ms=_latency_ms, db=_db
                )
            except Exception:
                pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                server_id=server_id,
            )

    async def call_tool_by_name(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> ToolCallResult:
        """Dispatch a tool by NAME, resolving which registered server exposes it.

        Convenience for callers that only know the tool name (e.g. workflow tool
        steps) rather than the server_id. ``tool_name`` may name the connection:
        ``"<connection name>.<tool>"`` or ``"<connection_slug>__<tool>"``. A bare
        name exposed by SEVERAL connections (two MongoDB connections both have
        ``mongodb_find``) is refused as ambiguous — it used to run on whichever
        connection was listed first.
        """
        from app.mcp.tool_naming import connection_slug, strip_connection_prefix

        try:
            records = await self._registry.list_server_records(tenant_ctx=tenant_ctx)
        except Exception as exc:
            return ToolCallResult(tool_name=tool_name, success=False, error=str(exc))

        def _wanted(conn_name: str) -> str | None:
            """The bare tool name if ``tool_name`` addresses this connection."""
            if not conn_name:
                return None
            if "." in tool_name:
                conn, _, bare = tool_name.rpartition(".")
                same = connection_slug(conn) == connection_slug(conn_name)
                return bare if same else None
            stripped = strip_connection_prefix(tool_name, conn_name)
            if stripped != tool_name:
                return stripped
            return None

        targeted: list[tuple[str, str]] = []
        bare_hits: list[tuple[str, str]] = []
        for server_id, cfg in records:
            conn_name = str(getattr(cfg, "name", "") or "")
            wanted = _wanted(conn_name)
            try:
                tools = await self.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
            except Exception:
                continue
            names = {getattr(t, "name", None) for t in tools}
            if wanted is not None and wanted in names:
                targeted.append((server_id, wanted))
            elif tool_name in names:
                bare_hits.append((server_id, conn_name or server_id))
        if len(targeted) == 1:
            server_id, bare = targeted[0]
            return await self.call_tool(
                server_id=server_id, tool_name=bare, arguments=arguments, tenant_ctx=tenant_ctx
            )
        if len(bare_hits) == 1 and not targeted:
            return await self.call_tool(
                server_id=bare_hits[0][0],
                tool_name=tool_name,
                arguments=arguments,
                tenant_ctx=tenant_ctx,
            )
        if len(bare_hits) > 1 or len(targeted) > 1:
            names = ", ".join(sorted(f"'{name}'" for _, name in bare_hits))
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=(
                    f"tool '{tool_name}' is exposed by several connectors ({names}); "
                    "name the connector, e.g. '<connector name>.<tool>'"
                ),
            )
        return ToolCallResult(
            tool_name=tool_name,
            success=False,
            error=f"no registered connector exposes tool '{tool_name}'",
        )

    async def _update_tool_stats(
        self,
        server_id: str,
        tool_name: str,
        tenant_id: str,
        success: bool,
        latency_ms: float,
        db: Any = None,
    ) -> None:
        """Update tool reliability statistics in tool_capabilities table."""
        if db is None:
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            col_success = "call_count = call_count + 1" + (
                ", error_count = error_count + 1" if not success else ""
            )
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(f"""
                    UPDATE tool_capabilities
                    SET {col_success},
                        avg_latency_ms = (avg_latency_ms * call_count + :lat) / (call_count + 1),
                        health_status = :status,
                        success_rate = CASE WHEN call_count + 1 > 0
                            THEN (call_count - error_count + :inc) * 1.0 / (call_count + 1)
                            ELSE 1.0 END,
                        updated_at = NOW()
                    WHERE tenant_id = :tid AND connector_id = :cid AND tool_name = :tool
                """),
                    {
                        "lat": latency_ms,
                        "tid": tenant_id,
                        "cid": server_id,
                        "tool": tool_name,
                        "status": "healthy" if success else "degraded",
                        "inc": 1 if success else 0,
                    },
                )
        except Exception as exc:
            logging.getLogger(__name__).debug("tool_stats_update_failed: %s", exc)

    async def _build_auth_headers(
        self,
        cfg: MCPServerConfig,
        *,
        tenant_ctx: TenantContext | None = None,
        server_id: str = "",
    ) -> dict[str, str]:
        """Build auth headers from server auth_config based on auth_type."""
        headers: dict[str, str] = {}
        auth = cfg.auth_config

        if cfg.auth_type == "bearer":
            token = await self._resolve_auth_value(auth.get("token", ""), tenant_ctx)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif cfg.auth_type == "api_key":
            key_name = str(auth.get("header_name", "X-API-Key"))
            key_value = await self._resolve_auth_value(auth.get("api_key", ""), tenant_ctx)
            if key_value:
                headers[key_name] = key_value
        elif cfg.auth_type == "basic":
            username = str(auth.get("username", ""))
            password = await self._resolve_auth_value(auth.get("password", ""), tenant_ctx)
            if username:
                creds = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {creds}"
        elif cfg.auth_type == "custom_header":
            for k, v in auth.items():
                if k != "auth_type":
                    headers[k] = await self._resolve_auth_value(v, tenant_ctx)
        elif cfg.auth_type in {"oauth_ac", "pkce", "oauth_cc"}:
            # Try to get token from OAuth manager
            if self._oauth_manager is not None:
                try:
                    tenant_id = getattr(tenant_ctx, "tenant_id", "")
                    # Read through the durable token store when the manager has
                    # one: the token may have been obtained by another replica
                    # (or this is the worker, which never ran the OAuth flow).
                    _aget = getattr(self._oauth_manager, "aget_token", None)
                    if inspect.iscoroutinefunction(_aget):
                        token = await _aget(tenant_id, server_id)
                    else:
                        token = self._oauth_manager.get_token(tenant_id, server_id)
                    if token is not None:
                        if token.is_expired():
                            with suppress(Exception):
                                token = await self._oauth_manager.refresh_token(
                                    tenant_id=tenant_id,
                                    server_id=server_id,
                                    token=token,
                                    auth_config=auth,
                                )
                        if token is not None and not token.is_expired():
                            headers["Authorization"] = f"Bearer {token.access_token}"
                except Exception:
                    pass

        return headers
