"""Tool inverse registry — maps tool names to their async undo functions.

Each inverse function receives the original tool arguments and performs
the actual API call to undo the tool's side effect.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

ROLLED_BACK = "rolled_back"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class InverseResult:
    """Outcome of one compensating action: ``rolled_back`` / ``skipped`` / ``failed``."""

    outcome: str
    detail: str = ""

# Registry: tool_name -> async callable(args, mcp_client) -> None
_INVERSE_REGISTRY: dict[str, Callable] = {}

# MCP client reference — set by main.py after wiring
_mcp_client: Any = None


def set_mcp_client(client: Any) -> None:
    """Wire the MCP client so inverses can make real API calls."""
    global _mcp_client
    _mcp_client = client


def register_inverse(tool_name: str, fn: Callable) -> None:
    _INVERSE_REGISTRY[tool_name] = fn


def get_inverse_fn(
    tool_name: str, arguments: dict[str, Any] | None = None
) -> Callable[..., Any] | None:
    """Return a callable that undoes the named tool call.

    Two modes depending on whether *arguments* is supplied:

    **New mode** (``arguments=None``, preferred):
        Returns an *async* callable ``inverse_fn(*args, **kwargs)`` wrapping
        the registered function, or ``None`` if no inverse is registered.
        Use this mode when the caller can ``await`` the result directly, e.g.
        in ``rollback_all_async(executed_tool_calls=...)``.  The wrapper
        forwards all positional/keyword arguments (including ``tool_call`` and
        ``mcp_client``) through to the registered function, includes
        ``tool_name`` in logged context, and swallows exceptions so one bad
        inverse never aborts the whole rollback sequence.

    **Legacy mode** (``arguments`` provided):
        Returns a zero-arg *sync* callable that schedules the real async work
        as a ``create_task`` when a running loop is present, or via
        ``asyncio.run()`` otherwise.  Registered functions may be:

        - sync 1-arg: ``lambda args: ...`` (old style, used in tests)
        - async 2-arg: ``async def fn(args, mcp_client): ...`` (built-ins)

        Returns ``lambda: None`` when no inverse is registered (never raises).
    """
    fn = _INVERSE_REGISTRY.get(tool_name)

    import asyncio
    import inspect

    # ── New mode: return awaitable or None ──────────────────────────────────
    if arguments is None:
        if fn is None:
            return None  # Caller should check for None before awaiting

        async def inverse_fn(*args: Any, **kwargs: Any) -> Any:
            try:
                if asyncio.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                result = fn(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            except Exception as exc:
                logger.warning(
                    "rollback_inverse_failed tool=%s error=%s",
                    tool_name,
                    str(exc)[:100],
                )
                # Report the failure instead of returning None, which the
                # rollback engine counted as a successful undo.
                return InverseResult(FAILED, str(exc)[:200])

        return inverse_fn

    # ── Legacy mode: zero-arg sync wrapper (backward-compat) ────────────────
    if fn is None:
        return lambda: None  # No inverse registered

    captured_args = dict(arguments)
    captured_client = _mcp_client

    async def _async_inverse() -> None:
        try:
            # Detect arity: new-style takes (args, mcp_client), old-style takes (args,)
            sig = inspect.signature(fn)
            if len(sig.parameters) >= 2:
                result = fn(captured_args, captured_client)
            else:
                result = fn(captured_args)
            # Await if the function returned a coroutine
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning("rollback_inverse_failed tool=%s error=%s", tool_name, str(exc))

    def _sync_wrapper() -> None:
        try:
            try:
                # If we're already inside a running loop (async context), schedule a task
                running_loop = asyncio.get_running_loop()
                running_loop.create_task(_async_inverse())  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
            except RuntimeError:
                # No running loop — we're in a sync context; create a fresh one
                asyncio.run(_async_inverse())
        except Exception as exc:
            logger.warning("rollback_wrapper_failed tool=%s error=%s", tool_name, str(exc))

    return _sync_wrapper


# ── Built-in inverses — make real MCP API calls ─────────────────────────────
#
# Each built-in returns an :class:`InverseResult` so the rollback engine can
# report honestly: ``rolled_back`` only when the compensating call succeeded,
# ``skipped`` when there was nothing identifiable to undo (e.g. the forward
# tool's output carried no id), ``failed`` when the undo call raised. They used
# to swallow every error and return None, which the engine counted as success.
#
# ``args`` is the forward call's input arguments merged with ``result`` (the
# forward call's OUTPUT, normalised to a dict) and ``server_id``. IDs of created
# objects live in the output, never in the input.


def _normalize_output(output: Any) -> dict[str, Any]:
    """Best-effort: turn an MCP tool output into a flat dict of fields.

    Handles plain dicts, JSON strings, ``{"result": {...}}`` wrappers and the MCP
    ``{"content": [{"type": "text", "text": "<json>"}]}`` envelope.
    """
    import json

    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (ValueError, TypeError):
            return {}
    if not isinstance(output, dict):
        return {}
    merged: dict[str, Any] = dict(output)
    inner = output.get("result")
    if isinstance(inner, (dict, str)):
        for k, v in _normalize_output(inner).items():
            merged.setdefault(k, v)
    content = output.get("content")
    if isinstance(content, (dict, str)):
        for k, v in _normalize_output(content).items():
            merged.setdefault(k, v)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                for k, v in _normalize_output(text if text is not None else part).items():
                    merged.setdefault(k, v)
    return merged


def _pick(args: dict[str, Any], *keys: str) -> Any:
    """First non-empty value for *keys* in the input args, then in the output."""
    for key in keys:
        val = args.get(key)
        if val:
            return val
    result = _normalize_output(args.get("result"))
    for key in keys:
        val = result.get(key)
        if val:
            return val
    return None


def _resolve_tenant_ctx(args: dict[str, Any], tenant_ctx: Any) -> Any:
    """The goal's real TenantContext. Legacy callers that only pass a tenant_id
    in ``args`` still get a context for that tenant; nothing falls back to a
    fabricated ``"rollback"`` tenant any more — no tenant means skip."""
    if tenant_ctx is not None:
        return tenant_ctx
    tenant_id = args.get("tenant_id")
    if not tenant_id:
        return None
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id=str(tenant_id), plan=PlanTier.FREE, api_key_id="rollback")


async def _call_undo(
    *,
    mcp_client: Any,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    tenant_ctx: Any,
    label: str,
) -> InverseResult:
    try:
        res = await mcp_client.call_tool(
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        logger.warning("%s_rollback_failed error=%s", label, str(exc))
        return InverseResult(FAILED, f"{tool_name}: {exc}")
    if getattr(res, "success", True) is False:
        err = str(getattr(res, "error", "") or "unsuccessful")
        logger.warning("%s_rollback_failed error=%s", label, err)
        return InverseResult(FAILED, f"{tool_name}: {err}")
    logger.info("%s_rolled_back arguments=%s", label, arguments)
    return InverseResult(ROLLED_BACK, f"{tool_name} {arguments}")


async def _inverse_jira_create_issue(
    args: dict, mcp_client: Any, *, tenant_ctx: Any = None
) -> InverseResult:
    """Delete a Jira issue that was created by the forward tool call."""
    issue_id = _pick(args, "issue_id", "id", "key", "issue_key")
    server_id = args.get("server_id", "")
    ctx = _resolve_tenant_ctx(args, tenant_ctx)
    if not issue_id or not mcp_client or not server_id or ctx is None:
        logger.info("jira_rollback_skipped reason=no_issue_id_or_mcp_client_or_tenant")
        return InverseResult(SKIPPED, "no issue id / mcp client / server / tenant")
    return await _call_undo(
        mcp_client=mcp_client,
        server_id=server_id,
        tool_name="jira_delete_issue",
        arguments={"issue_id": issue_id},
        tenant_ctx=ctx,
        label="jira",
    )


async def _inverse_confluence_create_page(
    args: dict, mcp_client: Any, *, tenant_ctx: Any = None
) -> InverseResult:
    """Delete a Confluence page that was created by the forward tool call."""
    page_id = _pick(args, "page_id", "id")
    server_id = args.get("server_id", "")
    ctx = _resolve_tenant_ctx(args, tenant_ctx)
    if not page_id or not mcp_client or not server_id or ctx is None:
        logger.info("confluence_rollback_skipped reason=no_page_id")
        return InverseResult(SKIPPED, "no page id / mcp client / server / tenant")
    return await _call_undo(
        mcp_client=mcp_client,
        server_id=server_id,
        tool_name="confluence_delete_page",
        arguments={"page_id": page_id},
        tenant_ctx=ctx,
        label="confluence",
    )


async def _inverse_slack_send_message(
    args: dict, mcp_client: Any, *, tenant_ctx: Any = None
) -> InverseResult:
    """Delete a Slack message that was sent by the forward tool call."""
    message_ts = _pick(args, "ts", "message_ts")
    channel = args.get("channel") or _normalize_output(args.get("result")).get("channel", "")
    server_id = args.get("server_id", "")
    ctx = _resolve_tenant_ctx(args, tenant_ctx)
    if not message_ts or not mcp_client or not server_id or ctx is None:
        logger.info("slack_rollback_skipped reason=no_message_ts")
        return InverseResult(SKIPPED, "no message ts / mcp client / server / tenant")
    return await _call_undo(
        mcp_client=mcp_client,
        server_id=server_id,
        tool_name="slack_delete_message",
        arguments={"ts": message_ts, "channel": channel},
        tenant_ctx=ctx,
        label="slack",
    )


async def _inverse_github_create_issue(
    args: dict, mcp_client: Any, *, tenant_ctx: Any = None
) -> InverseResult:
    """Close a GitHub issue that was created by the forward tool call."""
    issue_number = _pick(args, "issue_number", "number")
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    server_id = args.get("server_id") or "builtin-github"
    ctx = _resolve_tenant_ctx(args, tenant_ctx)
    if not all([owner, repo, issue_number]) or not mcp_client or ctx is None:
        logger.info("github_rollback_skipped reason=no_issue_number_or_owner_repo")
        return InverseResult(SKIPPED, "no issue number / owner / repo / mcp client / tenant")
    return await _call_undo(
        mcp_client=mcp_client,
        server_id=server_id,
        tool_name="github_close_issue",
        arguments={
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "state": "closed",
        },
        tenant_ctx=ctx,
        label="github",
    )


async def run_inverse(
    tool_names: list[str],
    *,
    arguments: dict[str, Any],
    output: Any,
    server_id: str,
    tenant_ctx: Any,
    mcp_client: Any = None,
) -> InverseResult:
    """Undo one executed tool call with its real OUTPUT and the goal's tenant.

    *tool_names* are candidate registry keys (e.g. the model's tool name and the
    connector's canonical name); the first registered one is used.
    """
    import inspect

    fn = next((_INVERSE_REGISTRY[n] for n in tool_names if n in _INVERSE_REGISTRY), None)
    if fn is None:
        return InverseResult(SKIPPED, f"no inverse registered for {tool_names[:1]}")
    payload: dict[str, Any] = dict(arguments)
    payload["result"] = output
    payload["server_id"] = server_id or payload.get("server_id", "")
    tenant_id = getattr(tenant_ctx, "tenant_id", None)
    if tenant_id:
        payload["tenant_id"] = tenant_id
    client = mcp_client if mcp_client is not None else _mcp_client
    try:
        params = inspect.signature(fn).parameters
        if "tenant_ctx" in params:
            res = fn(payload, client, tenant_ctx=tenant_ctx)
        elif len(params) >= 2:
            res = fn(payload, client)
        else:
            res = fn(payload)
        if inspect.isawaitable(res):
            res = await res
    except Exception as exc:
        return InverseResult(FAILED, str(exc)[:200])
    if isinstance(res, InverseResult):
        return res
    # Custom inverses that return nothing are trusted to have completed.
    return InverseResult(ROLLED_BACK, "")


# Register all built-in inverses
register_inverse("jira:create_issue", _inverse_jira_create_issue)
register_inverse("jira_create_issue", _inverse_jira_create_issue)
register_inverse("confluence:create_page", _inverse_confluence_create_page)
register_inverse("confluence_create_page", _inverse_confluence_create_page)
register_inverse("slack:send_message", _inverse_slack_send_message)
register_inverse("slack_send_message", _inverse_slack_send_message)
register_inverse("github:create_issue", _inverse_github_create_issue)
register_inverse("github_create_issue", _inverse_github_create_issue)
