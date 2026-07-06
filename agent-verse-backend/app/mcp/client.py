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
from app.net.ssrf_guard import SSRFError, assert_public_url
from app.providers.vault import is_connector_secret_ref, resolve_connector_secret_ref
from app.tenancy.context import TenantContext

logger = logging.getLogger(__name__)
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


def _extract_credentials_from_server(cfg: MCPServerConfig) -> dict[str, str]:
    """Extract credentials dict from an MCPServerConfig for passing to builtin handlers.

    auth_config entries are written first; the server-level URL always wins
    (prevents auth_config["url"] from overwriting the real server URL).
    """
    result: dict[str, str] = {}
    # auth_config entries first (connector-level credentials)
    for key, value in (cfg.auth_config or {}).items():
        if isinstance(value, str):
            result[key] = value
    # Server-level URL always wins over anything in auth_config
    for url_field in ("url", "base_url"):
        url_val = getattr(cfg, url_field, None)
        if url_val and url_val != "builtin://":
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


def _response_json(resp: httpx.Response) -> Any:
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:].strip())
    return {}


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
        self._secret_resolver_accepts_tenant = self._accepts_tenant_context(
            self._secret_resolver
        )
        # Circuit breaker support — wired externally by setting _redis
        self._circuit_breakers: dict[str, Any] = {}
        self._redis: Any = redis
        # LLM provider for self-healing tool argument repair
        self._provider: Any = llm_provider
        # OAuth manager — wired externally
        self._oauth_manager: Any = None
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

    async def _resolve_auth_value(
        self, value: Any, tenant_ctx: TenantContext | None
    ) -> str:
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
    ) -> dict[str, str]:
        session = self._mcp_sessions.get(cfg.server_id)
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
        self._mcp_sessions[cfg.server_id] = session
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
        if not server_id:
            logger.warning("discover_tools called with empty server_id")
            return []
        cfg = await self._registry.get(server_id, tenant_ctx=tenant_ctx)
        if cfg is None:
            return []

        # ── Builtin servers MUST be checked first ─────────────────────────────
        # The builtin_handler is a Python callable that is NOT serialised to Redis.
        # After a Redis round-trip it will be None, but the URL prefix "builtin://"
        # identifies these servers unambiguously. Always return their stored
        # tool_definitions, and lazily restore the handler from the process-local
        # MCPRegistry._BUILTIN_HANDLER_REGISTRY dict registered at startup / worker init.
        is_builtin = (
            cfg.builtin_handler is not None
            or (cfg.base_url or "").startswith("builtin://")
            or (cfg.url or "").startswith("builtin://")
        )
        if is_builtin and cfg.tool_definitions:
            # Lazily restore the builtin handler into this process if missing
            if cfg.builtin_handler is None:
                try:
                    from app.mcp.registry import MCPRegistry as _MCPReg
                    restored = _MCPReg.get_builtin_handler(server_id)
                    if restored is not None:
                        cfg = cfg.model_copy(update={"builtin_handler": restored})
                except Exception:
                    pass
            return [
                ToolDefinition(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    input_schema=t.get("parameters", t.get("inputSchema", t.get("input_schema", {}))),
                    server_id=server_id,
                    server_name=cfg.name,
                )
                for t in cfg.tool_definitions
                if t.get("name")
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

        headers = await self._build_auth_headers(
            cfg, tenant_ctx=tenant_ctx, server_id=server_id
        )
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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if is_mcp_endpoint:
                    resp = await client.post(
                        cfg.url.rstrip("/"),
                        json=_jsonrpc("tools/list"),
                        headers=headers,
                    )
                    if self._requires_initialize(resp):
                        headers = await self._ensure_mcp_session(client, cfg, headers)
                        resp = await client.post(
                            cfg.url.rstrip("/"),
                            json=_jsonrpc("tools/list"),
                            headers=headers,
                        )
                else:
                    resp = await client.get(f"{cfg.url.rstrip('/')}/tools", headers=headers)
                resp.raise_for_status()
                data = _response_json(resp)
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
                handler = _MCPReg.get_builtin_handler(server.server_id)
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
    ) -> ToolCallResult:
        """Dispatch an HTTP call for an OpenAPI-imported tool definition."""
        effective_base = server.base_url or server.url
        http_method = tool_def.get("http_method", "POST").upper()
        http_path = tool_def.get("http_path", "")
        tool_name = tool_def.get("name") or tool_def.get("tool_name", "")

        url = effective_base.rstrip("/") + "/" + http_path.lstrip("/")
        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if http_method == "GET":
                    resp = await client.get(url, params=arguments, headers=headers)
                else:
                    resp = await client.request(
                        http_method, url, json=arguments, headers=headers
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
        except Exception as exc:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
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

        headers = await self._build_auth_headers(
            server, tenant_ctx=tenant_ctx, server_id=server_id
        )
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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
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
        if cfg.builtin_handler is not None:
            return await self._dispatch_builtin_tool(cfg, tool_name, arguments)

        # SSRF guard — validate server URL before any outbound HTTP call
        _request_url = _absolute_http_url(cfg.url or cfg.base_url or "")
        if _request_url and not _request_url.startswith("builtin://"):
            try:
                assert_public_url(_request_url, context=f"MCP server {server_id}")
            except SSRFError as exc:
                logger.warning("ssrf_guard_blocked_mcp: server_id=%s, error=%s", server_id, str(exc))
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    error="Server URL blocked by SSRF guard",
                    server_id=server_id,
                )

        # 2. OpenAPI-imported tool stored in tool_definitions
        if cfg.tool_definitions:
            for tdef in cfg.tool_definitions:
                if tdef.get("name") == tool_name or tdef.get("tool_name") == tool_name:
                    return await self._dispatch_openapi_tool(cfg, tdef, arguments)

        # 3. Jira REST connector registered with an Atlassian base URL
        if _is_jira_rest_endpoint(cfg):
            return await self._dispatch_jira_rest_tool(
                cfg, server_id, tool_name, arguments, tenant_ctx
            )

        # 4. Normal MCP/HTTP dispatch
        headers = await self._build_auth_headers(
            cfg, tenant_ctx=tenant_ctx, server_id=server_id
        )
        headers["Content-Type"] = "application/json"
        using_jsonrpc = _is_mcp_endpoint(cfg.url)
        if using_jsonrpc:
            headers["Accept"] = "application/json, text/event-stream"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if using_jsonrpc:
                resp = await client.post(
                    cfg.url.rstrip("/"),
                    json=_jsonrpc(
                        "tools/call",
                        {"name": tool_name, "arguments": arguments},
                    ),
                    headers=headers,
                )
                if self._requires_initialize(resp):
                    headers = await self._ensure_mcp_session(client, cfg, headers)
                    resp = await client.post(
                        cfg.url.rstrip("/"),
                        json=_jsonrpc(
                            "tools/call",
                            {"name": tool_name, "arguments": arguments},
                        ),
                        headers=headers,
                    )
            else:
                resp = await client.post(
                    f"{cfg.url.rstrip('/')}/tools/{tool_name}",
                    json={"arguments": arguments},
                    headers=headers,
                )
            resp.raise_for_status()
            payload = _response_json(resp)
            is_jsonrpc_response = (
                using_jsonrpc
                or (
                    isinstance(payload, dict)
                    and payload.get("jsonrpc") == "2.0"
                )
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
                            if (
                                tdef.get("name") == tool_name
                                or tdef.get("tool_name") == tool_name
                            ):
                                return await self._dispatch_openapi_tool(
                                    server=server,
                                    tool_def=tdef,
                                    arguments=arguments,
                                )
            except Exception:
                pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"Server {server_id} not found",
            )

        _tenant_id = getattr(tenant_ctx, "tenant_id", "")
        _t0 = _time.monotonic()

        # ── Universal Intelligence Layer ───────────────────────────────────────
        # Step 1: Resolve arguments against the tool's JSON schema before the
        # first call so the LLM's parameter name variations are fixed upstream.
        try:
            from app.mcp.tool_intelligence import get_resolver, get_healer
            _resolver = get_resolver()
            _healer = get_healer(getattr(self, "_provider", None))

            # Get the tool schema — try multiple sources:
            # Source A: cfg.tool_definitions (builtin + OpenAPI servers)
            # Source B: live discover_tools() call (external MCP servers)
            _tool_schema: dict | None = None
            for _tdef in (cfg.tool_definitions or []):
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
            if not _tool_schema and not _is_mcp_endpoint(getattr(cfg, "url", "") or getattr(cfg, "base_url", "") or ""):
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
        except Exception:
            pass  # exfil guard must never block execution on error

        try:
            result = await self._call_tool_impl(
                cfg, server_id, tool_name, arguments, tenant_ctx
            )

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
                not result.success
                and _healer.is_argument_error(result.error)  # type: ignore[union-attr]
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
                    server_id, tool_name, _tenant_id,
                    success=result.success, latency_ms=_latency_ms, db=_db
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
                    server_id, tool_name, _tenant_id,
                    success=False, latency_ms=_latency_ms, db=_db
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
                    server_id, tool_name, _tenant_id,
                    success=False, latency_ms=_latency_ms, db=_db
                )
            except Exception:
                pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                server_id=server_id,
            )

    async def _update_tool_stats(
        self, server_id: str, tool_name: str, tenant_id: str,
        success: bool, latency_ms: float, db: Any = None
    ) -> None:
        """Update tool reliability statistics in tool_capabilities table."""
        if db is None:
            return
        try:
            from sqlalchemy import text

            col_success = "call_count = call_count + 1" + (
                ", error_count = error_count + 1" if not success else ""
            )
            async with db() as session, session.begin():
                await session.execute(text(f"""
                    UPDATE tool_capabilities
                    SET {col_success},
                        avg_latency_ms = (avg_latency_ms * call_count + :lat) / (call_count + 1),
                        health_status = :status,
                        success_rate = CASE WHEN call_count + 1 > 0
                            THEN (call_count - error_count + :inc) * 1.0 / (call_count + 1)
                            ELSE 1.0 END,
                        updated_at = NOW()
                    WHERE tenant_id = :tid AND connector_id = :cid AND tool_name = :tool
                """), {
                    "lat": latency_ms, "tid": tenant_id, "cid": server_id,
                    "tool": tool_name,
                    "status": "healthy" if success else "degraded",
                    "inc": 1 if success else 0,
                })
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
