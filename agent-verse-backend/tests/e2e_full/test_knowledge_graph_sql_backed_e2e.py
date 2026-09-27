"""e2e_full: the knowledge graph is served from Postgres, not from process memory.

``KnowledgeGraphStore`` used to hydrate each tenant's *entire* graph — every node
and every edge — into dicts on every replica, then answer search, neighbours,
paths, communities, stats and export from that copy, refreshed on a 30-second
timer with writes persisted fire-and-forget behind it. At corpus scale that is
the whole graph resident in every API replica and Celery worker.

These run the real (SQL) store and check four things:

* it holds no graph state in process after writes and reads;
* a write made through one store instance is visible to another immediately
  (the second instance stands in for a second replica — no hydration, no timer);
* every read answers exactly what the reference in-memory implementation
  answers for the same graph;
* tenant isolation holds on every read path.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from app.knowledge_graph.store import KnowledgeGraphStore, PersistedGraphRequiresAsyncError

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _graph(tenant_id: str) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Two components plus an isolated node.

    A: a0→a1→a2→a3 and a0→a2 (a shortcut), plus a3→a1 (a cycle)
    B: b0—b1 (one edge)
    c0: isolated
    """
    p = uuid.uuid4().hex[:6]
    ids = {k: f"{p}-{k}" for k in ("a0", "a1", "a2", "a3", "b0", "b1", "c0")}
    nodes = [
        GraphNode(ids["a0"], tenant_id, NodeType.ENTITY, "Acme Payments", "payments gateway", confidence=0.9),
        GraphNode(ids["a1"], tenant_id, NodeType.ENTITY, "Ledger Service", "double-entry ledger", confidence=0.8),
        GraphNode(ids["a2"], tenant_id, NodeType.CONCEPT, "Settlement", "nightly settlement run", confidence=0.7),
        GraphNode(ids["a3"], tenant_id, NodeType.TOOL, "reconcile.py", "reconciles ledger vs bank", confidence=0.6),
        GraphNode(ids["b0"], tenant_id, NodeType.ENTITY, "Onboarding", "KYC_flow step 1", confidence=0.5),
        GraphNode(ids["b1"], tenant_id, NodeType.DOCUMENT, "KYC Policy", "know your customer", confidence=0.4),
        GraphNode(ids["c0"], tenant_id, NodeType.MEMORY, "Lonely 100% node", "no edges", confidence=0.3),
    ]
    e = lambda s, t, k: GraphEdge(f"{p}-e-{s}-{t}", tenant_id, ids[s], ids[t], k)  # noqa: E731
    edges = [
        e("a0", "a1", EdgeType.DEPENDS_ON),
        e("a1", "a2", EdgeType.DEPENDS_ON),
        e("a2", "a3", EdgeType.USED_TOOL),
        e("a0", "a2", EdgeType.REFERENCES),
        e("a3", "a1", EdgeType.SUPPORTS),
        e("b0", "b1", EdgeType.REFERENCES),
    ]
    return nodes, edges


async def _pair(app: Any, tenant_client: Any) -> tuple[str, KnowledgeGraphStore, KnowledgeGraphStore]:
    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    writer = KnowledgeGraphStore()
    writer.set_db(app.state.db_session_factory)
    reader = KnowledgeGraphStore()  # "another replica": same DB, nothing in memory
    reader.set_db(app.state.db_session_factory)
    return tenant_id, writer, reader


def _reference(nodes: list[GraphNode], edges: list[GraphEdge]) -> KnowledgeGraphStore:
    ref = KnowledgeGraphStore()
    for n in nodes:
        ref.add_node(n)
    for e in edges:
        ref.add_edge(e)
    return ref


async def test_no_graph_state_is_held_in_process(app: Any, tenant_client: Any) -> None:
    tenant_id, writer, reader = await _pair(app, tenant_client)
    nodes, edges = _graph(tenant_id)
    await writer.aupsert(nodes, edges)
    await reader.aquery_nodes(tenant_id, limit=100)
    await reader.adetect_communities(tenant_id)
    await reader.afind_path(nodes[0].node_id, nodes[3].node_id, tenant_id)
    for store in (writer, reader):
        assert store._nodes == {} and store._edges == {}
        assert store._tenant_nodes == {} and store._tenant_edges == {}


async def test_writes_are_visible_to_another_replica_immediately(
    app: Any, tenant_client: Any
) -> None:
    tenant_id, writer, reader = await _pair(app, tenant_client)
    nodes, edges = _graph(tenant_id)
    await writer.aupsert(nodes, edges)
    got = await reader.aget_node(nodes[1].node_id, tenant_id)
    assert got is not None and got.label == "Ledger Service"
    assert len(await reader.aget_edges_for_node(nodes[1].node_id, tenant_id)) == 3


async def test_every_read_matches_the_reference_implementation(
    app: Any, tenant_client: Any
) -> None:
    tenant_id, writer, reader = await _pair(app, tenant_client)
    nodes, edges = _graph(tenant_id)
    await writer.aupsert(nodes, edges)
    ref = _reference(nodes, edges)
    ids = [n.node_id for n in nodes]

    # Search: case-insensitive substring over label and content, incl. LIKE
    # metacharacters that must be matched literally.
    for kwargs in (
        {},
        {"search": "LEDGER"},
        {"search": "kyc_"},
        {"search": "100%"},
        {"node_type": NodeType.ENTITY},
        {"min_confidence": 0.65},
        {"limit": 2},
    ):
        sql = [n.node_id for n in await reader.aquery_nodes(tenant_id, **kwargs)]
        mem = [n.node_id for n in ref.query_nodes(tenant_id, **kwargs)]
        assert sql == mem, f"query_nodes{kwargs}: sql={sql} mem={mem}"

    # Neighbours (undirected) as sets — order is not part of the contract.
    for nid in ids:
        sql_n = {(x["target"], x["relation"]) for x in await reader.aget_neighbors(nid, tenant_id)}
        mem_n = {(x["target"], x["relation"]) for x in ref.get_neighbors(nid, tenant_id)}
        assert sql_n == mem_n, nid

    # Paths (directed), including cycle handling and max_hops bounds.
    for src, dst, hops in ((0, 3, 3), (0, 3, 1), (1, 1, 3), (3, 2, 3), (0, 5, 4), (2, 0, 3)):
        sql_p = await reader.afind_path(ids[src], ids[dst], tenant_id, max_hops=hops)
        mem_p = ref.find_path(ids[src], ids[dst], tenant_id, max_hops=hops)
        assert sorted(sql_p) == sorted(mem_p), (src, dst, hops, sql_p, mem_p)
        assert all(len(p) - 1 <= hops for p in sql_p), f"path exceeds {hops} hops: {sql_p}"

    # Stats.
    sql_s = await reader.aget_graph_stats(tenant_id)
    mem_s = ref.get_graph_stats(tenant_id)
    assert sql_s["total_nodes"] == mem_s["total_nodes"] == 7
    assert sql_s["total_edges"] == mem_s["total_edges"] == 6
    assert sql_s["node_types"] == mem_s["node_types"]
    assert sql_s["avg_confidence"] == pytest.approx(mem_s["avg_confidence"])

    # Communities: same components, sizes, central nodes and density; the
    # isolated node is skipped.
    def _shape(cs: list[dict[str, Any]]) -> list[tuple[int, frozenset[str], str, float]]:
        return sorted(
            (c["size"], frozenset(c["node_ids"]), c["central_node"], c["density"]) for c in cs
        )

    sql_c = await reader.adetect_communities(tenant_id)
    assert _shape(sql_c) == _shape(ref.detect_communities(tenant_id))
    assert [c["size"] for c in sql_c] == [4, 2]
    # Stable ids across calls (the in-memory detector minted a fresh uuid4 each time).
    again = await reader.adetect_communities(tenant_id)
    assert [c["community_id"] for c in sql_c] == [c["community_id"] for c in again]


async def test_export_pages_through_the_whole_graph(app: Any, tenant_client: Any) -> None:
    tenant_id, writer, reader = await _pair(app, tenant_client)
    nodes, edges = _graph(tenant_id)
    await writer.aupsert(nodes, edges)

    seen_nodes: list[str] = []
    seen_edges: list[str] = []
    node_cur = edge_cur = None
    for _ in range(10):
        page = await reader.aexport(tenant_id, limit=3, node_cursor=node_cur, edge_cursor=edge_cur)
        seen_nodes += [n.node_id for n in page["nodes"]]
        seen_edges += [e.edge_id for e in page["edges"]]
        node_cur, edge_cur = page["next_node_cursor"], page["next_edge_cursor"]
        if node_cur is None and edge_cur is None:
            break
    assert sorted(seen_nodes) == sorted(n.node_id for n in nodes)
    assert sorted(seen_edges) == sorted(e.edge_id for e in edges)


async def test_every_read_path_is_tenant_isolated(app: Any, client: Any) -> None:
    async def _tenant() -> str:
        email = f"kg-{uuid.uuid4().hex[:12]}@example.com"
        r = await client.post("/tenants/signup", json={"name": "KG", "email": email})
        assert r.status_code == 201, r.text
        return str(r.json()["tenant_id"])

    tenant_a, tenant_b = await _tenant(), await _tenant()
    store = KnowledgeGraphStore()
    store.set_db(app.state.db_session_factory)
    nodes, edges = _graph(tenant_a)
    await store.aupsert(nodes, edges)
    ids = [n.node_id for n in nodes]

    assert await store.aquery_nodes(tenant_b, limit=100) == []
    assert await store.aget_node(ids[0], tenant_b) is None
    assert await store.aget_nodes(ids, tenant_b) == {}
    assert await store.aget_edges_for_node(ids[1], tenant_b) == []
    assert await store.afind_path(ids[0], ids[3], tenant_b) == []
    assert (await store.aget_graph_stats(tenant_b))["total_nodes"] == 0
    assert await store.adetect_communities(tenant_b) == []
    assert (await store.aexport(tenant_b))["nodes"] == []
    assert await store.acount_nodes(tenant_b) == 0
    assert await store.acount_nodes(tenant_a) == 7


async def test_sync_reads_refuse_rather_than_answer_from_nothing(app: Any) -> None:
    store = KnowledgeGraphStore()
    store.set_db(app.state.db_session_factory)
    with pytest.raises(PersistedGraphRequiresAsyncError):
        store.query_nodes("t", limit=5)
    with pytest.raises(PersistedGraphRequiresAsyncError):
        store.find_path("a", "b", "t")


async def test_api_endpoints_serve_from_the_database(app: Any, tenant_client: Any) -> None:
    """The HTTP surface, end to end: write through the API, read it back."""
    add = await tenant_client.post(
        "/knowledge-graph/nodes",
        json={"node_type": "entity", "label": "Quasar Billing", "content": "billing core"},
    )
    assert add.status_code == 200, add.text
    node_id = add.json()["node_id"]

    found = await tenant_client.get("/knowledge-graph/nodes", params={"search": "quasar"})
    assert found.status_code == 200
    assert [n["node_id"] for n in found.json()["nodes"]] == [node_id]

    detail = await tenant_client.get(f"/knowledge-graph/nodes/{node_id}")
    assert detail.status_code == 200 and detail.json()["node"]["label"] == "Quasar Billing"

    exported = await tenant_client.get("/knowledge-graph/export", params={"limit": 10})
    assert exported.status_code == 200
    assert node_id in [n["node_id"] for n in exported.json()["nodes"]]
