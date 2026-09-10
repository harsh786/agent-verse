"""Build a real tenant knowledge graph from an organisation's own structure.

The Graphify feature promises to "transform your org's knowledge into a graph".
This module makes that real: it reads the org's departments, teams, member
agents, missions, tasks and tools and materialises them as
:class:`~app.knowledge_graph.models.GraphNode` / ``GraphEdge`` records in the
tenant-scoped :data:`~app.knowledge_graph.store.kg_store`. Optionally it also
runs entity extraction (LLM or deterministic) over the org's free-text fields to
surface concepts that aren't part of the formal hierarchy.

Every node/edge id is a deterministic ``uuid5`` so re-running Graphify upserts
in place instead of duplicating. The store persists to Postgres best-effort, so
the graph survives restarts and is immediately visible to the Knowledge Graph
explorer and the Obsidian vault (both read ``kg_store`` for the tenant).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from app.knowledge_graph.store import kg_store

if TYPE_CHECKING:
    from app.org.service import OrgService

# Stable namespace so ids are reproducible across runs/processes.
_NS = uuid.UUID("6a5f6d2e-4b1a-4c3d-9e8f-0a1b2c3d4e5f")

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]

# Phase labels — kept in sync with the SSE contract the frontend animates.
_PHASES = [
    "Fetching org knowledge",
    "Extracting entities",
    "Building relationships",
    "Detecting communities",
    "Persisting graph",
]


def _nid(tenant_id: str, kind: str, ident: str) -> str:
    return str(uuid.uuid5(_NS, f"{tenant_id}:{kind}:{ident}"))


def _eid(tenant_id: str, src: str, etype: str, tgt: str) -> str:
    return str(uuid.uuid5(_NS, f"{tenant_id}:{src}:{etype}:{tgt}"))


class _GraphAccumulator:
    """Collects nodes/edges, deduping by id, and writes them to ``kg_store``."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, GraphEdge] = {}
        self._now = datetime.now(UTC).isoformat()

    def node(
        self,
        kind: str,
        ident: str,
        node_type: NodeType,
        label: str,
        *,
        content: str = "",
        confidence: float = 1.0,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        node_id = _nid(self.tenant_id, kind, ident)
        # First writer wins for identity, but keep the richest label/content.
        if node_id not in self._nodes:
            self._nodes[node_id] = GraphNode(
                node_id=node_id,
                tenant_id=self.tenant_id,
                node_type=node_type,
                label=(label or ident)[:200],
                content=content[:2000],
                source_id=source_id,
                confidence=confidence,
                metadata={"kind": kind, **(metadata or {})},
                created_at=self._now,
                updated_at=self._now,
            )
        return node_id

    def edge(
        self,
        src: str,
        tgt: str,
        edge_type: EdgeType,
        *,
        label: str = "",
        confidence: float = 1.0,
    ) -> None:
        # Skip self-loops and dangling edges (both endpoints must be real nodes).
        if src == tgt or src not in self._nodes or tgt not in self._nodes:
            return
        edge_id = _eid(self.tenant_id, src, edge_type.value, tgt)
        if edge_id not in self._edges:
            self._edges[edge_id] = GraphEdge(
                edge_id=edge_id,
                tenant_id=self.tenant_id,
                source_node_id=src,
                target_node_id=tgt,
                edge_type=edge_type,
                label=label,
                confidence=confidence,
                provenance="graphify:org",
                created_at=self._now,
            )

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def flush(self) -> None:
        """Write everything accumulated so far into the tenant store (upsert)."""
        for node in self._nodes.values():
            kg_store.add_node(node)
        for edge in self._edges.values():
            kg_store.add_edge(edge)


async def build_org_knowledge_graph(
    *,
    service: OrgService,
    tenant_id: str,
    org_id: str,
    provider: Any | None = None,
    emit: EmitFn | None = None,
    use_llm: bool = True,
) -> dict[str, int]:
    """Materialise the org's structure into the tenant knowledge graph.

    Emits SSE-shaped progress events via ``emit`` (``phase``/``stats``/
    ``complete``) and returns the final ``{nodes, edges, communities,
    discoveries}`` counts. Raises only for a genuinely missing org; all other
    steps degrade gracefully so a partial graph is still built and shown.
    """

    async def _emit(payload: dict[str, Any]) -> None:
        if emit is not None:
            await emit(payload)

    total = len(_PHASES)

    async def _phase(idx: int) -> None:
        await _emit(
            {"type": "phase", "phase": idx, "total_phases": total, "label": _PHASES[idx - 1]}
        )

    acc = _GraphAccumulator(tenant_id)
    discoveries = 0

    # ── Phase 1: fetch the org's real structure ──────────────────────────────
    await _phase(1)
    org = await service.get_organization(org_id)
    if org is None:
        raise ValueError(f"Organization {org_id} not found")

    departments = await service.list_departments(org_id)
    teams = await service.list_teams(org_id, status=None)
    missions = await service.list_missions(org_id, limit=200)
    tasks = await service.list_tasks(org_id, limit=500)

    # ── Phase 2: nodes (entities) ────────────────────────────────────────────
    await _phase(2)
    org_node = acc.node(
        "org",
        str(org.id),
        NodeType.CONCEPT,
        str(org.name),
        content=str(getattr(org, "description", "") or getattr(org, "mission", "") or ""),
        source_id=str(org.id),
        metadata={"industry": str(getattr(org, "industry", "") or "")},
    )

    dept_nodes: dict[str, str] = {}
    for dept in departments:
        dept_nodes[str(dept.id)] = acc.node(
            "dept",
            str(dept.id),
            NodeType.CONCEPT,
            str(dept.name),
            content=str(getattr(dept, "purpose", "") or ""),
            source_id=str(dept.id),
        )

    team_nodes: dict[str, str] = {}
    agent_nodes: dict[str, str] = {}
    tool_nodes: dict[str, str] = {}
    for team in teams:
        tnode = acc.node(
            "team",
            str(team.id),
            NodeType.AGENT,
            str(team.name),
            content=str(getattr(team, "purpose", "") or ""),
            source_id=str(team.id),
        )
        team_nodes[str(team.id)] = tnode
        for agent_id in getattr(team, "member_agent_ids", None) or []:
            agent_nodes.setdefault(
                str(agent_id),
                acc.node("agent", str(agent_id), NodeType.AGENT, str(agent_id)),
            )
        for tool_id in getattr(team, "tool_ids", None) or []:
            tool_nodes.setdefault(
                str(tool_id),
                acc.node("tool", str(tool_id), NodeType.TOOL, str(tool_id)),
            )

    mission_nodes: dict[str, str] = {}
    for mission in missions:
        mission_nodes[str(mission.id)] = acc.node(
            "mission",
            str(mission.id),
            NodeType.GOAL,
            str(mission.title),
            content=str(getattr(mission, "objective", "") or getattr(mission, "why", "") or ""),
            source_id=str(mission.id),
            metadata={
                "status": str(getattr(mission, "status", "")),
                "priority": str(getattr(mission, "priority", "")),
            },
        )

    task_nodes: dict[str, str] = {}
    for task in tasks:
        task_nodes[str(task.id)] = acc.node(
            "task",
            str(task.id),
            NodeType.GOAL,
            str(task.title),
            content=str(getattr(task, "objective", "") or ""),
            confidence=0.9,
            source_id=str(task.id),
            metadata={"status": str(getattr(task, "status", ""))},
        )
        for tool_id in getattr(task, "required_tools", None) or []:
            tool_nodes.setdefault(
                str(tool_id),
                acc.node("tool", str(tool_id), NodeType.TOOL, str(tool_id)),
            )

    # Optional: surface free-text concepts via entity extraction (best-effort).
    if provider is not None:
        try:
            from app.knowledge_graph.extractor import entity_extractor

            entity_extractor.set_provider(provider)
            corpus_parts = [
                str(getattr(org, "description", "") or ""),
                str(getattr(org, "mission", "") or ""),
                str(getattr(org, "vision", "") or ""),
            ]
            corpus_parts += [
                str(getattr(m, "objective", "") or getattr(m, "why", "") or "")
                for m in missions[:10]
            ]
            corpus = "\n".join(p for p in corpus_parts if p.strip())
            if corpus.strip():
                if use_llm:
                    entities = await entity_extractor.extract_entities_llm(
                        corpus, tenant_id, str(org.id)
                    )
                else:
                    entities = entity_extractor.extract_entities_deterministic(
                        corpus, tenant_id, str(org.id)
                    )
                for ent in entities[:30]:
                    ent_node = acc.node(
                        "entity",
                        ent.label.lower(),
                        NodeType.ENTITY,
                        ent.label,
                        content=ent.content,
                        confidence=ent.confidence,
                        source_id=str(org.id),
                    )
                    acc.edge(org_node, ent_node, EdgeType.MENTIONS, label="mentions")
                    discoveries += 1
        except Exception:
            # Extraction is a bonus; the structural graph is the guaranteed core.
            pass

    await _emit(
        {
            "type": "stats",
            "nodes": acc.node_count,
            "edges": acc.edge_count,
            "communities": 0,
            "discoveries": discoveries,
        }
    )

    # ── Phase 3: relationships (edges) ───────────────────────────────────────
    await _phase(3)
    for dept in departments:
        acc.edge(org_node, dept_nodes[str(dept.id)], EdgeType.PARENT_OF, label="department")

    for team in teams:
        tnode = team_nodes[str(team.id)]
        parent = dept_nodes.get(str(getattr(team, "dept_id", "") or ""), org_node)
        acc.edge(parent, tnode, EdgeType.PARENT_OF, label="team")
        for agent_id in getattr(team, "member_agent_ids", None) or []:
            acc.edge(tnode, agent_nodes[str(agent_id)], EdgeType.PARENT_OF, label="member")
        for tool_id in getattr(team, "tool_ids", None) or []:
            acc.edge(tnode, tool_nodes[str(tool_id)], EdgeType.USED_TOOL, label="tool")

    for mission in missions:
        mnode = mission_nodes[str(mission.id)]
        acc.edge(org_node, mnode, EdgeType.REFERENCES, label="mission")
        dept_id = str(getattr(mission, "dept_id", "") or "")
        if dept_id in dept_nodes:
            acc.edge(dept_nodes[dept_id], mnode, EdgeType.REFERENCES, label="owns")
        team_id = str(getattr(mission, "assigned_team_id", "") or "")
        if team_id in team_nodes:
            acc.edge(team_nodes[team_id], mnode, EdgeType.DEPENDS_ON, label="assigned")

    for task in tasks:
        tnode = task_nodes[str(task.id)]
        mission_id = str(getattr(task, "mission_id", "") or "")
        if mission_id in mission_nodes:
            acc.edge(mission_nodes[mission_id], tnode, EdgeType.PARENT_OF, label="task")
        parent_task_id = str(getattr(task, "parent_task_id", "") or "")
        if parent_task_id in task_nodes:
            acc.edge(task_nodes[parent_task_id], tnode, EdgeType.PARENT_OF, label="subtask")
        team_id = str(getattr(task, "assigned_team_id", "") or "")
        if team_id in team_nodes:
            acc.edge(team_nodes[team_id], tnode, EdgeType.DEPENDS_ON, label="assigned")
        for tool_id in getattr(task, "required_tools", None) or []:
            acc.edge(tnode, tool_nodes[str(tool_id)], EdgeType.USED_TOOL, label="tool")

    await _emit(
        {
            "type": "stats",
            "nodes": acc.node_count,
            "edges": acc.edge_count,
            "communities": 0,
            "discoveries": discoveries,
        }
    )

    # ── Phase 4 + 5: persist, then detect communities on the stored graph ────
    await _phase(4)
    acc.flush()
    try:
        communities = kg_store.detect_communities(tenant_id)
        community_count = len(communities)
    except Exception:
        community_count = 0

    await _phase(5)
    stats = kg_store.get_graph_stats(tenant_id)
    final = {
        "nodes": int(stats.get("total_nodes", acc.node_count)),
        "edges": int(stats.get("total_edges", acc.edge_count)),
        "communities": community_count,
        "discoveries": discoveries,
    }
    await _emit({"type": "complete", **final})
    return final
