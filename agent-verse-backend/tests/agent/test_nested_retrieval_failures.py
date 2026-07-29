"""Nested goal retrieval success and failure propagation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.goal_tree import execute_sub_goal
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, SubGoal
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext("nested-tenant", PlanTier.PROFESSIONAL, "nested-key")


@pytest.mark.asyncio
async def test_failed_child_state_sets_error_and_preserves_retrieval_trace() -> None:
    child = AgentState(goal="child", tenant_ctx=TENANT)
    child.status = GoalStatus.FAILED
    child.error_message = "Required retrieval failed"
    child.provenance = [{"citation_id": "collection-1:citation-1"}]
    child.context["rag_strategy_trace"] = [{"status": "failed"}]
    child.events.append({"type": "knowledge_retrieval_failed"})

    class Graph:
        async def run(self, **kwargs: Any) -> AgentState:
            return child

    result = await execute_sub_goal(
        SubGoal("child-1", "child", "parent"),
        tenant_ctx=TENANT,
        graph_factory=Graph,
        semaphore=asyncio.Semaphore(1),
    )

    assert result.status is GoalStatus.FAILED
    assert result.error == "Required retrieval failed"
    assert result.result == ""
    assert result.provenance == child.provenance
    assert result.retrieval_trace == [{"status": "failed"}]
    assert result.events == child.events


@pytest.mark.asyncio
async def test_parent_fails_and_emits_when_nested_retrieval_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = SubGoal("child-1", "child", "parent", status=GoalStatus.FAILED)
    failed.error = "Required retrieval failed"
    failed.provenance = [{"citation_id": "collection-1:citation-1"}]
    failed.retrieval_trace = [{"status": "failed"}]

    async def fake_execute_goal_tree(*args: Any, **kwargs: Any) -> list[SubGoal]:
        return [failed]

    monkeypatch.setattr("app.agent.goal_tree.execute_goal_tree", fake_execute_goal_tree)
    events: list[dict[str, Any]] = []

    async def record(event: dict[str, Any]) -> None:
        events.append(event)

    graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        enable_goal_tree=True,
        goal_tree_threshold=1,
    )
    graph._event_callback = record
    parent = AgentState(goal="parent", tenant_ctx=TENANT, plan=["child"])

    result = await graph._node_execute(
        {"agent_state": parent, "tenant_ctx": TENANT, "plan": ["child"]}
    )

    assert result["agent_state"].status is GoalStatus.FAILED
    assert result["agent_state"].error_message == "Nested retrieval failed"
    assert result["agent_state"].provenance == failed.provenance
    assert any(event["type"] == "nested_goal_failed" for event in events)


@pytest.mark.asyncio
async def test_nested_graph_inherits_success_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = []

    async def record(event: dict[str, Any]) -> None:
        events.append(event)

    async def fake_execute_goal_tree(*args: Any, **kwargs: Any) -> list[SubGoal]:
        child_graph = kwargs["graph_factory"]()
        await child_graph._emit(
            {
                "type": "knowledge_retrieved",
                "citations": [{"citation_id": "collection-1:citation-1"}],
            }
        )
        success = SubGoal("child-1", "child", "parent", status=GoalStatus.COMPLETE)
        success.provenance = [{"citation_id": "collection-1:citation-1"}]
        return [success]

    monkeypatch.setattr("app.agent.goal_tree.execute_goal_tree", fake_execute_goal_tree)
    graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        enable_goal_tree=True,
        goal_tree_threshold=1,
    )
    graph._event_callback = record
    parent = AgentState(goal="parent", tenant_ctx=TENANT, plan=["child"])

    await graph._node_execute(
        {"agent_state": parent, "tenant_ctx": TENANT, "plan": ["child"]}
    )

    assert any(event["type"] == "knowledge_retrieved" for event in events)
    assert parent.provenance == [{"citation_id": "collection-1:citation-1"}]


@pytest.mark.asyncio
async def test_full_run_nested_retrieval_failure_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = SubGoal("child-1", "child", "parent", status=GoalStatus.FAILED)
    failed.error = "Required retrieval failed"
    failed.retrieval_trace = [{"status": "failed"}]

    async def fake_execute_goal_tree(*args: Any, **kwargs: Any) -> list[SubGoal]:
        return [failed]

    monkeypatch.setattr("app.agent.goal_tree.execute_goal_tree", fake_execute_goal_tree)
    provider = FakeProvider(
        responses=[
            '{"steps": ["child"]}',
            '{"success": true, "reason": "would overwrite failure"}',
        ]
    )
    graph = AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        enable_goal_tree=True,
        goal_tree_threshold=1,
    )

    result = await graph.run(goal="parent", tenant_ctx=TENANT)

    assert result.status is GoalStatus.FAILED
    assert result.error_message == "Nested retrieval failed"
    assert not result.verification_success


@pytest.mark.asyncio
async def test_execute_sub_goal_forwards_parent_callback_once() -> None:
    received: list[dict[str, Any]] = []

    async def callback(event: dict[str, Any]) -> None:
        received.append(event)

    class Graph:
        async def run(self, *, event_callback: Any = None, **kwargs: Any) -> AgentState:
            assert event_callback is callback
            await event_callback(
                {
                    "type": "knowledge_retrieved",
                    "provenance": [{"citation_id": "collection-1:citation-1"}],
                }
            )
            state = AgentState(goal="child", tenant_ctx=TENANT)
            state.status = GoalStatus.COMPLETE
            state.provenance = [{"citation_id": "collection-1:citation-1"}]
            return state

    result = await execute_sub_goal(
        SubGoal("child-1", "child", "parent"),
        tenant_ctx=TENANT,
        graph_factory=Graph,
        semaphore=asyncio.Semaphore(1),
        event_callback=callback,
    )

    assert result.status is GoalStatus.COMPLETE
    assert received == [
        {
            "type": "knowledge_retrieved",
            "provenance": [{"citation_id": "collection-1:citation-1"}],
        }
    ]


@pytest.mark.asyncio
async def test_successful_child_preserves_callbacks_and_provenance() -> None:
    child = AgentState(goal="child", tenant_ctx=TENANT)
    child.status = GoalStatus.COMPLETE
    child.provenance = [{"citation_id": "collection-1:citation-1"}]
    child.context["rag_strategy_trace"] = [{"status": "complete"}]
    child.events.append({"type": "knowledge_retrieved"})

    class Graph:
        async def run(self, **kwargs: Any) -> AgentState:
            return child

    result = await execute_sub_goal(
        SubGoal("child-1", "child", "parent"),
        tenant_ctx=TENANT,
        graph_factory=Graph,
        semaphore=asyncio.Semaphore(1),
    )

    assert result.status is GoalStatus.COMPLETE
    assert result.provenance == child.provenance
    assert result.retrieval_trace == [{"status": "complete"}]
    assert result.events == child.events
