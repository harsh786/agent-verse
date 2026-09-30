"""Outbound A2A call tool — lets agents delegate a task to an external A2A agent.

Exposed to the agent loop as the ``builtin-a2a`` built-in server (registered in
``app.mcp.servers.registry_wiring`` with the other built-in agent tools) whose one
tool, ``a2a_delegate_task``, calls the tenant's REGISTERED A2A agent:

* The endpoint and bearer token come only from the calling tenant's own "A2A
  Agent" connector (``POST /connectors``; the token is vault-stored and resolved
  per tenant by ``MCPClient._dispatch_builtin_tool``). A model-chosen endpoint in
  the tool arguments is ignored, and nothing is wired from platform env.
* SSRF-guarded twice: the MCP client validates the connector URL before any
  dispatch, and :func:`call_external_a2a_agent` re-checks it and connects through
  the pinned client (no DNS rebinding, no redirects).
* Governed like every tool call: ``write_high`` risk (``tool_risk``), metered by
  the ``tool_call_complete`` event, and time-boxed (:data:`MAX_A2A_TOOL_TIMEOUT_S`).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.net.ssrf_guard import SSRFError, assert_public_url, public_async_client
from app.observability.logging import get_logger

logger = get_logger(__name__)

SERVER_ID = "builtin-a2a"
SERVER_NAME = "A2A Agent"
SERVER_DESCRIPTION = (
    "Delegate a task to your registered external A2A-compatible agent and return "
    "its answer."
)
# Not a platform credential: an endpoint only the tenant can supply. Listing it
# keeps the server off every tenant's surface until the tenant registers its own
# "A2A Agent" connector (see registry_wiring.register_builtin_servers).
REQUIRES_ENV: tuple[str, ...] = ("A2A_AGENT_URL",)
TOOL_NAME = "a2a_delegate_task"
DEFAULT_A2A_TOOL_TIMEOUT_S = 60.0
MAX_A2A_TOOL_TIMEOUT_S = 120.0

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": TOOL_NAME,
        "description": (
            "Send a natural-language task to the tenant's registered external A2A "
            "agent and return its answer. The data you include leaves this "
            "platform."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the external agent, in plain language.",
                },
                "context": {
                    "type": "object",
                    "description": "Optional structured context passed to the agent.",
                },
            },
            "required": ["task"],
        },
    }
]


def _timeout_from(credentials: dict[str, Any]) -> float:
    try:
        requested = float(credentials.get("timeout") or DEFAULT_A2A_TOOL_TIMEOUT_S)
    except (TypeError, ValueError):
        requested = DEFAULT_A2A_TOOL_TIMEOUT_S
    if requested <= 0:
        requested = DEFAULT_A2A_TOOL_TIMEOUT_S
    return min(requested, MAX_A2A_TOOL_TIMEOUT_S)


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Built-in server handler (``MCPClient._dispatch_builtin_tool`` contract).

    ``credentials`` are the calling tenant's registered connector: ``url`` (the
    agent endpoint) and optionally ``token``. Failures come back as
    ``{"error": ...}`` (a failed tool call), never raised.
    """
    if tool_name != TOOL_NAME:
        return {"error": f"Unknown A2A tool: {tool_name!r}"}
    creds = credentials or {}
    endpoint = str(creds.get("url") or creds.get("base_url") or "").strip()
    if not endpoint:
        return {
            "error": "No registered A2A agent: add an 'A2A Agent' connector with its endpoint."
        }
    args = arguments or {}
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        return {"error": "a2a_delegate_task needs a non-empty 'task'."}
    context = args.get("context")
    token = next(
        (
            str(creds[k])
            for k in ("token", "api_key", "bearer_token", "auth_token")
            if isinstance(creds.get(k), str) and creds[k]
        ),
        "",
    )
    result = await call_external_a2a_agent(
        agent_endpoint=endpoint,
        task_description=task.strip(),
        context=context if isinstance(context, dict) else None,
        auth_token=token,
        timeout=_timeout_from(creds),
    )
    if result.get("status") != "completed":
        return {"error": str(result.get("output") or result.get("error") or "A2A call failed")}
    output = result.get("output")
    return {"output": output if isinstance(output, str) else str(output), "status": "completed"}


async def call_external_a2a_agent(
    *,
    agent_endpoint: str,
    task_description: str,
    context: dict[str, Any] | None = None,
    auth_token: str = "",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call an external A2A-compatible agent and wait for the result.

    Args:
        agent_endpoint: The A2A endpoint URL.
        task_description: Natural language task for the external agent.
        context: Optional context dict passed through to the remote agent.
        auth_token: Bearer token for the external agent (empty = anonymous).
        timeout: Request timeout in seconds.

    Returns:
        dict with ``"output"``, ``"status"``, and optionally ``"artifacts"``
        or ``"error"``.
    """
    # SSRF guard — external agent endpoints must be public
    try:
        assert_public_url(agent_endpoint, context="A2A outbound call")
    except SSRFError as exc:
        logger.warning("a2a_outbound_ssrf_blocked", url=agent_endpoint[:100])
        return {
            "output": str(exc),
            "status": "error",
            "error": "URL blocked by security policy",
        }

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        # Pinned to the address validated at connect time: a plain client
        # re-resolved the endpoint (DNS rebinding past the check above).
        # Redirects are not followed (as before).
        async with public_async_client(timeout=timeout) as client:
            resp = await client.post(
                agent_endpoint,
                json={
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": task_description}],
                    },
                    "context": context or {},
                },
                headers=headers,
            )

        if resp.status_code >= 400:
            logger.warning(
                "a2a_outbound_error",
                status=resp.status_code,
                endpoint=agent_endpoint[:100],
            )
            return {
                "output": f"External agent returned error {resp.status_code}",
                "status": "error",
                "http_status": resp.status_code,
            }

        data: dict[str, Any] = resp.json()
        # Extract output from A2A response format — probe common field names
        output = (
            data.get("output")
            or data.get("result")
            or data.get("message", {}).get("content", "")
            or str(data)
        )

        return {
            "output": output,
            "status": "completed",
            "raw": data,
        }

    except httpx.TimeoutException:
        return {
            "output": f"External agent timed out after {timeout}s",
            "status": "timeout",
        }
    except Exception as exc:
        logger.warning("a2a_outbound_exception", error=str(exc)[:100])
        return {"output": str(exc), "status": "error"}
