"""Graphify builds a REAL tenant knowledge graph from org structure.

Regression for the "graphify graphs are not showing anywhere" bug: the org
Graphify job used to emit fake stats and persist nothing, so the Knowledge Graph
explorer and Obsidian vault (both read ``kg_store``) stayed empty. The builder
must materialise the org hierarchy (org → dept → team → agent/tool; mission →
task) into ``kg_store`` and emit real phase/complete events.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.knowledge_graph.models import NodeType
from app.knowledge_graph.org_builder import build_org_knowledge_graph
from app.knowledge_graph.store import kg_store


class _FakeOrgService:
    def __init__(self, org, depts, teams, missions, tasks) -> None:
        self._org = org
        self._depts = depts
        self._teams = teams
        self._missions = missions
        self._tasks = tasks

    async def get_organization(self, org_id: str):
        return self._org if str(self._org.id) == org_id else None

    async def list_departments(self, org_id: str):
        return self._depts

    async def list_teams(self, org_id: str, *, dept_id=None, status="active"):
        return self._teams

    async def list_missions(self, org_id: str, *, limit=50, **kw):
        return self._missions

    async def list_tasks(self, org_id: str, *, limit=100, **kw):
        return self._tasks


def _fixture(tenant_id: str):
    org = SimpleNamespace(
        id="org-1", name="Acme", description="A widget company", mission="",
        vision="", industry="manufacturing",
    )
    dept = SimpleNamespace(id="dept-1", name="Engineering", purpose="Builds things")
    team = SimpleNamespace(
        id="team-1", name="Platform", purpose="Core", dept_id="dept-1",
        member_agent_ids=["agent-1"], tool_ids=["tool-1"],
    )
    mission = SimpleNamespace(
        id="mission-1", title="Ship v2", objective="Release the platform", why="",
        dept_id="dept-1", assigned_team_id="team-1", status="active", priority="high",
    )
    task = SimpleNamespace(
        id="task-1", title="Write migration", objective="", mission_id="mission-1",
        parent_task_id=None, assigned_team_id="team-1", required_tools=["tool-2"],
        status="running",
    )
    return _FakeOrgService(org, [dept], [team], [mission], [task])


@pytest.mark.asyncio
async def test_build_org_graph_persists_nodes_and_edges() -> None:
    tenant_id = f"tenant-{uuid.uuid4()}"
    svc = _fixture(tenant_id)
    events: list[dict] = []

    final = await build_org_knowledge_graph(
        service=svc, tenant_id=tenant_id, org_id="org-1", provider=None,
        emit=lambda e: _record(events, e),
    )

    # 8 structural nodes: org, dept, team, agent, tool-1, tool-2, mission, task.
    stats = kg_store.get_graph_stats(tenant_id)
    assert stats["total_nodes"] == 8
    assert stats["total_edges"] >= 10
    assert final["nodes"] == 8
    assert final["edges"] == stats["total_edges"]

    # Node-type coverage drives the graph's filter chips + colours.
    assert kg_store.query_nodes(tenant_id, node_type=NodeType.GOAL)  # mission + task
    assert kg_store.query_nodes(tenant_id, node_type=NodeType.AGENT)  # team + member
    assert kg_store.query_nodes(tenant_id, node_type=NodeType.TOOL)

    # SSE contract: five phases + a terminal complete event.
    assert [e["type"] for e in events].count("phase") == 5
    assert events[-1]["type"] == "complete"
    assert events[-1]["nodes"] == 8


@pytest.mark.asyncio
async def test_build_org_graph_is_idempotent() -> None:
    """Re-running Graphify upserts by deterministic id — no duplication."""
    tenant_id = f"tenant-{uuid.uuid4()}"
    svc = _fixture(tenant_id)

    await build_org_knowledge_graph(
        service=svc, tenant_id=tenant_id, org_id="org-1", provider=None,
    )
    first = kg_store.get_graph_stats(tenant_id)
    await build_org_knowledge_graph(
        service=svc, tenant_id=tenant_id, org_id="org-1", provider=None,
    )
    second = kg_store.get_graph_stats(tenant_id)

    assert first["total_nodes"] == second["total_nodes"]
    assert first["total_edges"] == second["total_edges"]


@pytest.mark.asyncio
async def test_build_org_graph_missing_org_raises() -> None:
    svc = _fixture("t")
    with pytest.raises(ValueError):
        await build_org_knowledge_graph(
            service=svc, tenant_id="t", org_id="does-not-exist", provider=None,
        )


async def _record(events: list[dict], e: dict) -> None:
    events.append(e)
