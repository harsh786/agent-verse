"""MCP HTTP client — discovers and calls tools on registered MCP servers.

Follows the MCP (Model Context Protocol) spec:
- GET /tools -> returns list of available tools
- POST /tools/{tool_name} -> executes a tool with given arguments
"""
from __future__ import annotations

import base64
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from app.mcp.registry import MCPRegistry, MCPServerConfig
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
    return url.rstrip("/").endswith("/mcp")


def _jsonrpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


class MCPClient:
    """HTTP client for calling tools on registered MCP servers."""

    def __init__(
        self,
        registry: MCPRegistry,
        timeout: float = 30.0,
        secret_resolver: SecretResolver | None = None,
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
        self._redis: Any = None
        # OAuth manager — wired externally
        self._oauth_manager: Any = None

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

    def _get_circuit_breaker(self, server_id: str) -> Any:
        """Return (or create) a RedisCircuitBreaker for the given server.

        Returns None when Redis is not configured so the call path is unaffected.
        """
        if self._redis is None:
            return None
        if server_id not in self._circuit_breakers:
            try:
                from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
                self._circuit_breakers[server_id] = RedisCircuitBreaker(
                    redis_client=self._redis,
                    tenant_id="",
                    tool_name=f"mcp:{server_id}",
                    failure_threshold=5,
                    cooldown_seconds=60.0,
                )
            except Exception:
                return None
        return self._circuit_breakers[server_id]

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

    async def discover_tools(
        self, *, server_id: str, tenant_ctx: TenantContext
    ) -> list[ToolDefinition]:
        """Discover available tools on a registered MCP server."""
        cfg = await self._registry.get(server_id, tenant_ctx=tenant_ctx)
        if cfg is None:
            return []

        headers = await self._build_auth_headers(
            cfg, tenant_ctx=tenant_ctx, server_id=server_id
        )
        is_mcp_endpoint = _is_mcp_endpoint(cfg.url)
        if is_mcp_endpoint:
            headers["Accept"] = "application/json, text/event-stream"
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if is_mcp_endpoint:
                    resp = await client.post(
                        cfg.url.rstrip("/"),
                        json=_jsonrpc("tools/list"),
                        headers=headers,
                    )
                else:
                    resp = await client.get(f"{cfg.url.rstrip('/')}/tools", headers=headers)
                resp.raise_for_status()
                data = resp.json()
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
            # Access internal Redis index to enumerate server IDs for this tenant.
            server_ids: set[str] = await self._registry._redis.smembers(
                self._registry._index_key(tenant_ctx.tenant_id)
            )
            for sid in server_ids:
                # Real Redis returns bytes; the in-memory stub returns str.
                sid_str = sid.decode() if isinstance(sid, bytes) else str(sid)
                tools = await self.discover_tools(server_id=sid_str, tenant_ctx=tenant_ctx)
                all_tools.extend(tools)
        except Exception as exc:
            logger.warning("discover_all_tools failed: %s", exc)
        return all_tools

    async def _call_tool_impl(
        self,
        cfg: MCPServerConfig,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> ToolCallResult:
        """Inner HTTP call logic — raises on any error for circuit-breaker accounting."""
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
            else:
                resp = await client.post(
                    f"{cfg.url.rstrip('/')}/tools/{tool_name}",
                    json={"arguments": arguments},
                    headers=headers,
                )
            resp.raise_for_status()
            payload = resp.json()
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
        # Circuit breaker check — raises CircuitBreakerOpenError if open
        cb = self._get_circuit_breaker(server_id)
        if cb is not None:
            try:
                if not await cb.can_call_async():
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker open for {server_id}. Retrying after cooldown."
                    )
            except CircuitBreakerOpenError:
                raise
            except Exception:
                pass  # CB check failure must never block tool calls

        cfg = await self._registry.get(server_id, tenant_ctx=tenant_ctx)
        if cfg is None:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"Server {server_id} not found",
            )

        try:
            result = await self._call_tool_impl(
                cfg, server_id, tool_name, arguments, tenant_ctx
            )
            if cb is not None:
                try:
                    await cb.record_success_async()
                except Exception:
                    pass
            return result
        except CircuitBreakerOpenError:
            raise
        except httpx.HTTPStatusError as exc:
            if cb is not None:
                try:
                    await cb.record_failure_async()
                except Exception:
                    pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                server_id=server_id,
            )
        except Exception as exc:
            if cb is not None:
                try:
                    await cb.record_failure_async()
                except Exception:
                    pass
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                server_id=server_id,
            )

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
                            try:
                                token = await self._oauth_manager.refresh_token(
                                    tenant_id=tenant_id,
                                    server_id=server_id,
                                    token=token,
                                    auth_config=auth,
                                )
                            except Exception:
                                pass
                        if token is not None and not token.is_expired():
                            headers["Authorization"] = f"Bearer {token.access_token}"
                except Exception:
                    pass

        return headers
