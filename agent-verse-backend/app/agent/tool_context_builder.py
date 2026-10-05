"""One planner tool-context builder for the in-process and the Celery worker path.

``GoalService._build_tool_context`` (web) and ``_build_worker_mcp_context``
(``app.scaling.tasks``) used to build different tool sets for the same goal:

* the web ToolRef never carried the connector's ``auto_approve`` (TOOLCTX-01);
* with no agent the web path offered RPA tools only while the worker offered every
  tenant connector (TOOLCTX-02);
* RPA tools were offered even where Playwright is not installed, so the planner
  planned ``rpa_*`` steps that can only fail (TOOLCTX-03);
* one unreachable connector raised out of the worker's discovery and failed the
  whole goal, where the web path recorded it and continued (TOOLCTX-06);
* the worker never applied the tiered ToolSelector, so a tenant with many
  connectors got every discovered tool in the prompt (TOOLCTX-07).

Both paths now call :func:`build_goal_tool_context`.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from app.agent.tool_context import ToolContext, ToolRef
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Upper bound on connectors discovered for a goal that names none (no agent):
# one keyset page of the tenant's registry, never an unbounded fan-out.
MAX_TENANT_CONNECTORS = 50


def rpa_tools_available() -> bool:
    """True when this process can actually run ``rpa_*`` tools (Playwright installed).

    Without Playwright the agent's RPA executor fails every command closed
    ("NOT IMPLEMENTED: requires a real browser"), so offering the tools only
    makes the planner plan steps that cannot succeed.
    """
    return importlib.util.find_spec("playwright") is not None


async def _connector_ids_for(
    registry: Any, tenant_ctx: Any, connector_ids: list[str] | None
) -> tuple[list[str], dict[str, Any]]:
    """Explicit ids as given; ``None`` = the tenant's connectors (one bounded page)."""
    if connector_ids is not None:
        return [str(c) for c in connector_ids], {}
    if registry is None:
        return [], {}
    page = await registry.list_page(tenant_ctx=tenant_ctx, limit=MAX_TENANT_CONNECTORS + 1)
    ids = [sid for sid, _ in page]
    if len(ids) > MAX_TENANT_CONNECTORS:
        logger.warning(
            "tool_context_tenant_connectors_truncated tenant=%s limit=%d",
            getattr(tenant_ctx, "tenant_id", ""),
            MAX_TENANT_CONNECTORS,
        )
        return ids[:MAX_TENANT_CONNECTORS], {"connectors_truncated": MAX_TENANT_CONNECTORS}
    return ids, {}


async def build_goal_tool_context(
    *,
    registry: Any,
    mcp_client: Any,
    tenant_ctx: Any,
    connector_ids: list[str] | None,
    goal: str = "",
    tool_selector: Any = None,
    agent: dict[str, Any] | None = None,
    include_rpa: bool | None = None,
) -> ToolContext:
    """The planner's tools for one goal.

    ``connector_ids`` — the agent's connectors; ``None`` when the goal has no
    agent, which offers the tenant's registered connectors (bounded). A
    connector whose discovery fails is recorded in ``connector_errors`` and the
    others are still offered. ``include_rpa`` defaults to Playwright availability.
    """
    from app.mcp.tool_naming import qualify_colliding_tools
    from app.rpa.tools import rpa_tool_refs

    if include_rpa is None:
        include_rpa = rpa_tools_available()
    tools: list[ToolRef] = rpa_tool_refs() if include_rpa else []

    metadata: dict[str, Any] = dict(agent or {})
    connector_errors: list[dict[str, str]] = []
    try:
        ids, extra = await _connector_ids_for(registry, tenant_ctx, connector_ids)
        metadata.update(extra)
    except Exception as exc:
        logger.warning("tool_context_connector_listing_failed error=%s", exc)
        ids = []
        connector_errors.append({"connector_id": "*", "error": f"listing failed: {exc}"})

    for connector_id in ids:
        try:
            cfg = (
                await registry.get(connector_id, tenant_ctx=tenant_ctx)
                if registry is not None
                else None
            )
            if registry is not None and cfg is None:
                connector_errors.append({"connector_id": connector_id, "error": "not found"})
                continue
            discovered = await mcp_client.discover_tools(
                server_id=connector_id, tenant_ctx=tenant_ctx
            )
        except Exception as exc:
            connector_errors.append({"connector_id": connector_id, "error": str(exc)[:300]})
            continue
        cfg_name = str(getattr(cfg, "name", "") or "")
        auto_approve = bool(getattr(cfg, "auto_approve", False))
        for item in discovered:
            name = str(getattr(item, "name", "") or "")
            if not name:
                continue
            schema = getattr(item, "input_schema", {})
            tools.append(
                ToolRef(
                    server_id=connector_id,
                    server_name=str(getattr(item, "server_name", "") or cfg_name or connector_id),
                    name=name,
                    description=str(getattr(item, "description", "") or ""),
                    input_schema=schema if isinstance(schema, dict) else {},
                    auto_approve=auto_approve,
                )
            )

    if connector_errors:
        metadata["connector_errors"] = connector_errors
        logger.warning(
            "tool_context_connector_errors tenant=%s count=%d",
            getattr(tenant_ctx, "tenant_id", ""),
            len(connector_errors),
        )

    # Same tool on several connections: distinct "<connection>__<tool>" names.
    all_tools = qualify_colliding_tools(tools)

    if tool_selector is not None and goal and all_tools:
        try:
            from app.agent.tool_context import to_tiered_prompt

            selection = await tool_selector.select(
                goal=goal, tools=all_tools, tenant_ctx=tenant_ctx
            )
            return ToolContext(
                connectors=[metadata],
                tools=selection.selected,
                tool_prompt_override=to_tiered_prompt(selection),
            )
        except Exception as exc:
            logger.warning("tool_selector_failed_fallback_to_full error=%s", str(exc)[:120])

    return ToolContext(connectors=[metadata], tools=all_tools)


def build_worker_tool_selector() -> Any:
    """The same goal-aware ToolSelector the API wires, for the Celery worker."""
    try:
        from app.agent.tool_selector import ToolSelector
        from app.mcp.capability_search import CapabilitySearch
        from app.providers.embedder_factory import build_query_embedder

        return ToolSelector(capability_search=CapabilitySearch(embedder=build_query_embedder()))
    except Exception as exc:
        logger.warning("worker_tool_selector_unavailable error=%s", exc)
        return None


__all__ = [
    "MAX_TENANT_CONNECTORS",
    "build_goal_tool_context",
    "build_worker_tool_selector",
    "rpa_tools_available",
]
