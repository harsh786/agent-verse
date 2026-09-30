"""CORE-10: goal-tree children do not write failing checkpoints; decomposition is guarded.

Child goal ids (``{parent}-{sg}-{hex}``) are longer than ``goals.id`` and have no
goals row, so every child checkpoint write violated the FK and was swallowed —
a failing DB round trip per step. Children are re-run from the parent's own
checkpoints, so child checkpointing is disabled explicitly. The decomposition
call used ``planner.complete`` directly: uncharged and without a circuit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.providers.guarded_completion import GuardedDecisionProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-tree", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _ExplodingFactory:
    """A DB session factory that must never be opened."""

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> Any:
        self.opened += 1
        raise AssertionError("child checkpoint must not touch the database")


async def test_child_graph_has_checkpointing_disabled() -> None:
    graph = AgentGraph(
        planner=FakeProvider(responses=["p"]),
        executor=FakeProvider(responses=["e"]),
        verifier=FakeProvider(responses=['{"success": true}']),
    )
    graph._checkpoints_enabled = False
    factory = _ExplodingFactory()
    graph._db_session_factory = factory
    state = AgentState(goal="child", tenant_ctx=T)

    await graph._write_checkpoint("parent-sg1-abcd1234", 0, state, T)
    assert await graph._load_checkpoint("parent-sg1-abcd1234", T) is None
    assert factory.opened == 0


async def test_goal_tree_children_and_decomposition_wiring() -> None:
    captured: dict[str, Any] = {}

    async def _fake_tree(goal: str, **kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        captured["child"] = kwargs["graph_factory"]()
        return []

    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["a", "b", "c", "d", "e"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        enable_goal_tree=True,
        goal_tree_threshold=1,
    )
    with patch("app.agent.goal_tree.execute_goal_tree", _fake_tree):
        await graph.run(goal="big goal", tenant_ctx=T, goal_id="g-parent")

    assert captured, "goal tree was not invoked"
    planner = captured["planner"]
    assert isinstance(planner, GuardedDecisionProvider)
    assert planner._goal_id == "g-parent"
    assert captured["child"]._checkpoints_enabled is False
