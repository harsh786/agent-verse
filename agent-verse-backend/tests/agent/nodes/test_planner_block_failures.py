"""CORE-22: failing optional planner blocks are logged, not silently swallowed.

The cost auto-downgrade used ``except Exception: pass``, so a cost-store outage
silently kept the expensive planning model.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-plan", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _BrokenCost:
    async def get_cost_tier(self, **kwargs: Any) -> str:
        raise ConnectionError("cost store down")

    def __getattr__(self, name: str) -> Any:  # other cost-controller calls are no-ops
        async def _noop(*a: Any, **k: Any) -> Any:
            return True

        return _noop


async def test_cost_tier_outage_is_logged_and_emitted() -> None:
    router = MagicMock()
    router.model_for_goal.return_value = "big-model"
    router.model_for.return_value = "small-model"
    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do it"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        model_router=router,
        cost_controller=_BrokenCost(),
    )
    graph._logger = MagicMock()
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    graph._event_callback = _cb  # type: ignore[assignment]
    state = AgentState(goal="plan something", tenant_ctx=T)

    await graph._node_plan({"agent_state": state, "tenant_ctx": T})

    warned = [
        c for c in graph._logger.warning.call_args_list if c.args[:1] == ("planner_block_failed",)
    ]
    assert any(c.kwargs.get("block") == "cost_tier" for c in warned), warned
    assert any(e.get("type") == "cost_tier_unavailable" for e in events)
