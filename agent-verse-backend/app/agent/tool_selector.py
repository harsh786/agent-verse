"""
ToolSelector — goal-aware top-k tool retrieval with reliability boosting.

Replaces the current approach of injecting ALL tools into every planner prompt.
Instead: embed the goal, score tools by semantic similarity + historical reliability,
select top-k, and render three tiers:
  - selected (top_k):     full JSON schema
  - signature (next 20):  one-line "name(args) → desc"
  - names_only (rest):    "name: desc"

Falls back to full list when total tools < min_tools_for_retrieval (15).
RPA tools excluded unless goal/agent signals browser work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolSelection:
    selected: list[Any]       # ToolRef — full schema tier
    signature: list[Any]      # ToolRef — one-line tier
    names_only: list[Any]     # ToolRef — name+desc tier
    rpa_included: bool = False

    def all_tools(self) -> list[Any]:
        return self.selected + self.signature + self.names_only


_RPA_KEYWORDS = frozenset({
    "navigate", "browser", "screenshot", "click", "scrape",
    "fill form", "download page", "open url", "web page", "website"
})


def _needs_rpa(goal: str, agent_capabilities: set[str] | None = None) -> bool:
    """Return True when the goal or agent capabilities signal browser work."""
    goal_lower = goal.lower()
    if agent_capabilities and "browser" in agent_capabilities:
        return True
    return any(kw in goal_lower for kw in _RPA_KEYWORDS)


class ToolSelector:
    def __init__(
        self,
        *,
        capability_search: Any,              # CapabilitySearch instance
        reliability: Any | None = None,      # ToolReliabilityStore | None
        top_k: int = 12,
        signature_k: int = 20,
        min_tools_for_retrieval: int = 15,
    ) -> None:
        self._cap_search = capability_search
        self._reliability = reliability
        self._top_k = top_k
        self._sig_k = signature_k
        self._min_for_retrieval = min_tools_for_retrieval

    async def select(
        self,
        *,
        goal: str,
        tools: list[Any],            # list[ToolRef]
        tenant_ctx: Any,
        agent_capabilities: set[str] | None = None,
    ) -> ToolSelection:
        """
        Select tools for a goal. Returns a ToolSelection with 3 tiers.
        Falls back to full list when < min_tools_for_retrieval tools exist.
        """
        # Filter RPA tools based on goal/capabilities
        rpa = _needs_rpa(goal, agent_capabilities)
        filtered = [t for t in tools if rpa or getattr(t, "server_id", "") != "rpa"]

        # Fallback: too few tools to bother with retrieval
        if len(filtered) <= self._min_for_retrieval:
            return ToolSelection(
                selected=filtered,
                signature=[],
                names_only=[],
                rpa_included=rpa,
            )

        # Score by capability search
        scored = await self._score_by_capability(goal, filtered, tenant_ctx)

        # Boost by reliability if available
        if self._reliability is not None:
            scored = await self._boost_by_reliability(scored, tenant_ctx)

        # Partition into tiers
        selected = [t for _, t in scored[: self._top_k]]
        sig_end = self._top_k + self._sig_k
        signature = [t for _, t in scored[self._top_k : sig_end]]
        names_only = [t for _, t in scored[sig_end:]]

        return ToolSelection(
            selected=selected,
            signature=signature,
            names_only=names_only,
            rpa_included=rpa,
        )

    async def _score_by_capability(
        self,
        goal: str,
        tools: list[Any],
        tenant_ctx: Any,
    ) -> list[tuple[float, Any]]:
        """Return [(score, tool)] sorted desc by relevance score."""
        try:
            # CapabilitySearch expects tool dicts
            tool_dicts = [
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "server_id": getattr(t, "server_id", ""),
                    "server_name": getattr(t, "server_name", ""),
                }
                for t in tools
            ]
            matches = await self._cap_search.search(
                query=goal,
                tools=tool_dicts,
                tenant_ctx=tenant_ctx,
                top_k=len(tools),
            )
            # Build name→tool lookup
            name_to_tool = {getattr(t, "name", ""): t for t in tools}
            scored = []
            matched_names: set[str] = set()
            for m in matches:
                tool_name = getattr(m, "tool_name", "") or getattr(m, "name", "")
                t = name_to_tool.get(tool_name)
                if t is not None:
                    scored.append((getattr(m, "score", 0.0), t))
                    matched_names.add(tool_name)
            # Include unmatched tools at score 0
            for t in tools:
                if getattr(t, "name", "") not in matched_names:
                    scored.append((0.0, t))
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored
        except Exception as exc:
            logger.debug("tool_selector_capability_search_failed", error=str(exc)[:80])
            return [(0.0, t) for t in tools]

    async def _boost_by_reliability(
        self,
        scored: list[tuple[float, Any]],
        tenant_ctx: Any,
    ) -> list[tuple[float, Any]]:
        """Boost scores by historical success rate."""
        reliability = self._reliability
        if reliability is None:
            return scored
        boosted = []
        for cap_score, tool in scored:
            tool_name = getattr(tool, "name", "")
            try:
                rel = await reliability.get_reliability(
                    tenant_id=getattr(tenant_ctx, "tenant_id", ""),
                    tool_name=tool_name,
                )
                success_rate = float((rel or {}).get("success_rate", 0.5))
            except Exception:
                success_rate = 0.5
            final = cap_score * (0.5 + 0.5 * success_rate)
            boosted.append((final, tool))
        boosted.sort(key=lambda x: x[0], reverse=True)
        return boosted
