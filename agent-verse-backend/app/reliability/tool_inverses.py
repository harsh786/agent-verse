"""Tool inverse registry — maps tool names to their async undo functions.

Each inverse function receives the original tool arguments and performs
the actual API call to undo the tool's side effect.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

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


def get_inverse_fn(tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
    """Return a zero-arg callable that undoes the named tool call.

    Backward-compatible: registered functions may be:
    - sync 1-arg: ``lambda args: ...`` (old style, used in tests)
    - async 2-arg: ``async def fn(args, mcp_client): ...`` (new style, built-ins)
    """
    fn = _INVERSE_REGISTRY.get(tool_name)
    if fn is None:
        return lambda: None  # No inverse registered
    captured_args = dict(arguments)
    captured_client = _mcp_client

    import asyncio
    import inspect

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
                running_loop.create_task(_async_inverse())
            except RuntimeError:
                # No running loop — we're in a sync context; create a fresh one
                asyncio.run(_async_inverse())
        except Exception as exc:
            logger.warning("rollback_wrapper_failed tool=%s error=%s", tool_name, str(exc))

    return _sync_wrapper


# ── Built-in inverses — make real MCP API calls ─────────────────────────────

async def _inverse_jira_create_issue(args: dict, mcp_client: Any) -> None:
    """Delete a Jira issue that was created by the forward tool call."""
    issue_id = (
        args.get("issue_id")
        or args.get("id")
        or (args.get("result", {}).get("id") if isinstance(args.get("result"), dict) else None)
    )
    server_id = args.get("server_id", "")
    if not issue_id or not mcp_client or not server_id:
        logger.info(
            "jira_rollback_skipped reason=no_issue_id_or_mcp_client args=%s", args
        )
        return
    try:
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(
            tenant_id=args.get("tenant_id", "rollback"),
            plan=PlanTier.FREE,
            api_key_id="rollback",
        )
        await mcp_client.call_tool(
            server_id=server_id,
            tool_name="jira_delete_issue",
            arguments={"issue_id": issue_id},
            tenant_ctx=ctx,
        )
        logger.info("jira_issue_rolled_back issue_id=%s", issue_id)
    except Exception as exc:
        logger.warning("jira_rollback_failed issue_id=%s error=%s", issue_id, str(exc))


async def _inverse_confluence_create_page(args: dict, mcp_client: Any) -> None:
    """Delete a Confluence page that was created by the forward tool call."""
    page_id = args.get("page_id") or args.get("id")
    server_id = args.get("server_id", "")
    if not page_id or not mcp_client or not server_id:
        logger.info("confluence_rollback_skipped reason=no_page_id")
        return
    try:
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(
            tenant_id=args.get("tenant_id", "rollback"),
            plan=PlanTier.FREE,
            api_key_id="rollback",
        )
        await mcp_client.call_tool(
            server_id=server_id,
            tool_name="confluence_delete_page",
            arguments={"page_id": page_id},
            tenant_ctx=ctx,
        )
        logger.info("confluence_page_rolled_back page_id=%s", page_id)
    except Exception as exc:
        logger.warning("confluence_rollback_failed page_id=%s error=%s", page_id, str(exc))


async def _inverse_slack_send_message(args: dict, mcp_client: Any) -> None:
    """Delete a Slack message that was sent by the forward tool call."""
    message_ts = args.get("ts") or args.get("message_ts")
    channel = args.get("channel", "")
    server_id = args.get("server_id", "")
    if not message_ts or not mcp_client or not server_id:
        logger.info("slack_rollback_skipped reason=no_message_ts")
        return
    try:
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(
            tenant_id=args.get("tenant_id", "rollback"),
            plan=PlanTier.FREE,
            api_key_id="rollback",
        )
        await mcp_client.call_tool(
            server_id=server_id,
            tool_name="slack_delete_message",
            arguments={"ts": message_ts, "channel": channel},
            tenant_ctx=ctx,
        )
        logger.info("slack_message_rolled_back message_ts=%s", message_ts)
    except Exception as exc:
        logger.warning(
            "slack_rollback_failed message_ts=%s error=%s", message_ts, str(exc)
        )


async def _inverse_github_create_issue(args: dict, mcp_client: Any) -> None:
    """Close/delete a GitHub issue that was created by the forward tool call."""
    issue_number = (
        args.get("issue_number")
        or args.get("number")
        or (
            args.get("result", {}).get("issue_number")
            if isinstance(args.get("result"), dict)
            else None
        )
        or (
            args.get("result", {}).get("number")
            if isinstance(args.get("result"), dict)
            else None
        )
    )
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    server_id = args.get("server_id", "builtin-github")

    if not all([owner, repo, issue_number]) or not mcp_client:
        logger.info(
            "github_rollback_skipped reason=no_issue_number_or_owner_repo args=%s", args
        )
        return
    try:
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(
            tenant_id=args.get("tenant_id", "rollback"),
            plan=PlanTier.FREE,
            api_key_id="rollback",
        )
        await mcp_client.call_tool(
            server_id=server_id,
            tool_name="github_close_issue",
            arguments={
                "owner": owner,
                "repo": repo,
                "issue_number": issue_number,
                "state": "closed",
            },
            tenant_ctx=ctx,
        )
        logger.info(
            "github_issue_rolled_back owner=%s repo=%s issue_number=%s",
            owner,
            repo,
            issue_number,
        )
    except Exception as exc:
        logger.warning(
            "github_rollback_failed owner=%s repo=%s issue_number=%s error=%s",
            owner,
            repo,
            issue_number,
            str(exc),
        )


# Register all built-in inverses
register_inverse("jira:create_issue", _inverse_jira_create_issue)
register_inverse("jira_create_issue", _inverse_jira_create_issue)
register_inverse("confluence:create_page", _inverse_confluence_create_page)
register_inverse("confluence_create_page", _inverse_confluence_create_page)
register_inverse("slack:send_message", _inverse_slack_send_message)
register_inverse("slack_send_message", _inverse_slack_send_message)
register_inverse("github:create_issue", _inverse_github_create_issue)
register_inverse("github_create_issue", _inverse_github_create_issue)
