"""Tenant-scoped knowledge graph store: Postgres-backed, in-memory for dev only.

With a database wired (``set_db``, done by the lifespan) this store keeps **no
graph state in the process**. Every read is a bounded, tenant-scoped SQL query
run under ``sqlalchemy_rls_context``; every write is an awaited, batched upsert.
Traversals run level-by-level against indexed adjacency, and community detection
runs as label propagation *inside* Postgres, so neither ever materialises the
graph in Python.

This replaced a design in which each replica hydrated the tenant's **entire**
graph — every node and every edge — into dicts, then answered every query
(search, neighbours, paths, communities, stats, export) from that copy. At the
scale the platform targets (a graph extracted from millions of documents) that
is the whole graph resident in every API replica and every Celery worker, kept
coherent only by a 30-second re-hydration timer, with writes persisted
fire-and-forget behind it.

Without a database (unit tests, single-process dev) the same public API is
served from in-process dicts. The async ``a*`` methods work in both modes and
are what application code calls; the sync read methods are the dev-mode API and
refuse to run against a persisted store rather than silently answering from
nothing — the same convention ``KnowledgeStore`` uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType

_log = logging.getLogger(__name__)

# Rows per executemany round-trip in a batched upsert.
_UPSERT_BATCH = 500
# Hard ceilings so no single request can turn into an unbounded scan.
_MAX_QUERY_LIMIT = 1000
_MAX_EDGES_PER_NODE = 1000
_MAX_EXPORT_PAGE = 5000
# find_path: nodes expanded per BFS level, and ids per ANY(...) probe.
_PATH_FRONTIER_CAP = 5000
_PATH_PROBE_CHUNK = 1000
# Community detection: label-propagation iterations (≈ graph diameter to converge),
# communities returned, and members listed per community.
_CC_MAX_ITERATIONS = 64
_CC_MAX_COMMUNITIES = 100
_CC_MAX_MEMBERS_LISTED = 1000

_NODE_COLS = (
    "id, tenant_id, node_type, label, content, source_id, confidence, "
    "extra_metadata, created_at, updated_at"
)
_EDGE_COLS = (
    "id, tenant_id, source_node_id, target_node_id, edge_type, label, confidence, "
    "evidence, provenance, created_at"
)

# Community ids are derived from (tenant, component root) so the same component
# keeps the same id across calls — the previous uuid4 changed on every request.
_COMMUNITY_NS = uuid.UUID("6f3c2a1e-9b7d-4c8e-a5f0-2d1b3e4c5a6f")


class PersistedGraphRequiresAsyncError(RuntimeError):
    """A sync read was called on a DB-backed store; use the ``a*`` method."""


def _like_pattern(search: str) -> str:
    """``%search%`` with LIKE metacharacters escaped (ESCAPE '\\')."""
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _row_to_node(r: Any) -> GraphNode | None:
    try:
        meta = r[7]
        if isinstance(meta, str):
            meta = json.loads(meta or "{}")
        return GraphNode(
            node_id=str(r[0]),
            tenant_id=str(r[1]),
            node_type=NodeType(r[2]),
            label=str(r[3]),
            content=r[4] or "",
            source_id=r[5],
            confidence=float(r[6] if r[6] is not None else 1.0),
            metadata=dict(meta or {}),
            created_at=_iso(r[8]),
            updated_at=_iso(r[9]),
        )
    except Exception as exc:  # malformed legacy row — skip, never fail the query
        _log.debug("kg_skip_malformed_node: %s", exc)
        return None


def _row_to_edge(r: Any) -> GraphEdge | None:
    try:
        return GraphEdge(
            edge_id=str(r[0]),
            tenant_id=str(r[1]),
            source_node_id=str(r[2]),
            target_node_id=str(r[3]),
            edge_type=EdgeType(r[4]),
            label=r[5] or "",
            confidence=float(r[6] if r[6] is not None else 1.0),
            evidence=r[7] or "",
            provenance=r[8] or "",
            created_at=_iso(r[9]),
        )
    except Exception as exc:
        _log.debug("kg_skip_malformed_edge: %s", exc)
        return None


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class KnowledgeGraphStore:
    """Tenant-scoped knowledge graph. Postgres is the source of truth when wired."""

    def __init__(self) -> None:
        # Dev/no-database mode only. With a DB wired these stay empty: the store
        # holds no graph state in the process.
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, GraphEdge] = {}
        self._tenant_nodes: dict[str, set[str]] = {}
        self._tenant_edges: dict[str, set[str]] = {}
        self._db: Any = None  # set via set_db() in lifespan

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def set_db(self, db_factory: Any) -> None:
        """Wire an async SQLAlchemy session factory; from here on Postgres serves.

        Anything a dev-mode caller put in the in-process dicts before wiring is
        dropped, so the two modes can never be mixed in one process.
        """
        self._db = db_factory
        if db_factory is not None:
            self._nodes.clear()
            self._edges.clear()
            self._tenant_nodes.clear()
            self._tenant_edges.clear()

    @property
    def persisted(self) -> bool:
        return self._db is not None

    def _require_memory_mode(self, method: str) -> None:
        if self._db is not None:
            raise PersistedGraphRequiresAsyncError(
                f"KnowledgeGraphStore.{method} is the in-memory (dev) API; with a "
                f"database wired use a{method}() — the graph is not held in process."
            )

    @asynccontextmanager
    async def _session(self, tenant_id: str) -> AsyncIterator[Any]:
        """A transaction scoped to *tenant_id* by RLS (``app.tenant_id`` GUC)."""
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            yield session

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add_node(self, node: GraphNode) -> None:
        """Upsert a node. Prefer :meth:`aupsert` — it is batched and awaited.

        With a database wired this schedules the write and returns; nothing is
        kept in process. Kept for callers that cannot await.
        """
        if self._db is None:
            self._nodes[node.node_id] = node
            self._tenant_nodes.setdefault(node.tenant_id, set()).add(node.node_id)
            return
        self._schedule(self.aupsert([node], []))

    def add_edge(self, edge: GraphEdge) -> None:
        """Upsert an edge. Prefer :meth:`aupsert`."""
        if self._db is None:
            self._edges[edge.edge_id] = edge
            self._tenant_edges.setdefault(edge.tenant_id, set()).add(edge.edge_id)
            return
        self._schedule(self.aupsert([], [edge]))

    @staticmethod
    def _schedule(coro: Any) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            _log.warning("kg_write_dropped_no_event_loop")
            return
        task.add_done_callback(
            lambda t: (
                _log.warning("kg_background_write_failed: %s", t.exception())
                if not t.cancelled() and t.exception() is not None
                else None
            )
        )

    async def aupsert(self, nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> int:
        """Batch-upsert nodes then edges; returns the number of elements written.

        One transaction per tenant, ``executemany`` in batches of 500. This is
        the write path at ingestion scale: the previous design opened one session
        per node and per edge, fire-and-forget, which under a bulk load is a
        connection storm whose failures were logged at DEBUG and lost.
        """
        if not nodes and not edges:
            return 0
        if self._db is None:
            for n in nodes:
                self._nodes[n.node_id] = n
                self._tenant_nodes.setdefault(n.tenant_id, set()).add(n.node_id)
            for e in edges:
                self._edges[e.edge_id] = e
                self._tenant_edges.setdefault(e.tenant_id, set()).add(e.edge_id)
            return len(nodes) + len(edges)

        from sqlalchemy import text

        by_tenant: dict[str, tuple[list[GraphNode], list[GraphEdge]]] = {}
        for n in nodes:
            by_tenant.setdefault(n.tenant_id, ([], []))[0].append(n)
        for e in edges:
            by_tenant.setdefault(e.tenant_id, ([], []))[1].append(e)

        now = datetime.now(UTC)
        written = 0
        for tenant_id, (t_nodes, t_edges) in by_tenant.items():
            async with self._session(tenant_id) as session:
                for batch in _chunks(t_nodes, _UPSERT_BATCH):
                    await session.execute(
                        text(
                            """
                            INSERT INTO knowledge_nodes
                                (id, tenant_id, node_type, label, content, source_id,
                                 confidence, extra_metadata, created_at, updated_at)
                            VALUES
                                (:id, :tenant_id, :node_type, :label, :content, :source_id,
                                 :confidence, CAST(:metadata AS json), :now, :now)
                            ON CONFLICT (id) DO UPDATE SET
                                label          = EXCLUDED.label,
                                content        = EXCLUDED.content,
                                confidence     = EXCLUDED.confidence,
                                extra_metadata = EXCLUDED.extra_metadata,
                                updated_at     = EXCLUDED.updated_at
                            WHERE knowledge_nodes.tenant_id = EXCLUDED.tenant_id
                            """
                        ),
                        [
                            {
                                "id": n.node_id,
                                "tenant_id": n.tenant_id,
                                "node_type": n.node_type.value,
                                "label": n.label[:500],
                                "content": n.content,
                                "source_id": n.source_id,
                                "confidence": n.confidence,
                                "metadata": json.dumps(n.metadata or {}),
                                "now": now,
                            }
                            for n in batch
                        ],
                    )
                    written += len(batch)
                for batch in _chunks(t_edges, _UPSERT_BATCH):
                    await session.execute(
                        text(
                            """
                            INSERT INTO knowledge_edges
                                (id, tenant_id, source_node_id, target_node_id,
                                 edge_type, label, confidence, evidence, provenance,
                                 created_at)
                            VALUES
                                (:id, :tenant_id, :src, :tgt, :edge_type, :label,
                                 :confidence, :evidence, :provenance, :now)
                            ON CONFLICT (id) DO NOTHING
                            """
                        ),
                        [
                            {
                                "id": e.edge_id,
                                "tenant_id": e.tenant_id,
                                "src": e.source_node_id,
                                "tgt": e.target_node_id,
                                "edge_type": e.edge_type.value,
                                "label": (e.label or "")[:255],
                                "confidence": e.confidence,
                                "evidence": e.evidence,
                                "provenance": (e.provenance or "")[:255],
                                "now": now,
                            }
                            for e in batch
                        ],
                    )
                    written += len(batch)
        return written

    async def delete_tenant_graph(self, tenant_id: str) -> None:
        """Delete all graph data for a tenant (edges first, then nodes)."""
        if self._db is None:
            for nid in list(self._tenant_nodes.get(tenant_id, set())):
                self._nodes.pop(nid, None)
            for eid in list(self._tenant_edges.get(tenant_id, set())):
                self._edges.pop(eid, None)
            self._tenant_nodes.pop(tenant_id, None)
            self._tenant_edges.pop(tenant_id, None)
            return
        from sqlalchemy import text

        async with self._session(tenant_id) as session:
            await session.execute(
                text("DELETE FROM knowledge_edges WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await session.execute(
                text("DELETE FROM knowledge_nodes WHERE tenant_id = :tid"), {"tid": tenant_id}
            )

    # ------------------------------------------------------------------
    # Async reads — the application API (both modes)
    # ------------------------------------------------------------------

    async def aget_node(self, node_id: str, tenant_id: str) -> GraphNode | None:
        found = await self.aget_nodes([node_id], tenant_id)
        return found.get(node_id)

    async def aget_nodes(self, node_ids: Iterable[str], tenant_id: str) -> dict[str, GraphNode]:
        """Batch lookup by id; unknown and foreign-tenant ids are simply absent."""
        ids = list(dict.fromkeys(str(i) for i in node_ids if i))
        if not ids:
            return {}
        if self._db is None:
            out: dict[str, GraphNode] = {}
            for nid in ids:
                node = self._nodes.get(nid)
                if node is not None and node.tenant_id == tenant_id:
                    out[nid] = node
            return out
        from sqlalchemy import text

        result: dict[str, GraphNode] = {}
        async with self._session(tenant_id) as session:
            for batch in _chunks(ids, _PATH_PROBE_CHUNK):
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {_NODE_COLS} FROM knowledge_nodes "
                            "WHERE tenant_id = :tid AND id = ANY(:ids)"
                        ),
                        {"tid": tenant_id, "ids": list(batch)},
                    )
                ).fetchall()
                for r in rows:
                    node = _row_to_node(r)
                    if node is not None:
                        result[node.node_id] = node
        return result

    async def aquery_nodes(
        self,
        tenant_id: str,
        node_type: NodeType | None = None,
        search: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[GraphNode]:
        """Nodes by type / substring / confidence, highest confidence first."""
        limit = max(1, min(int(limit), _MAX_QUERY_LIMIT))
        if self._db is None:
            return self._memory_query_nodes(tenant_id, node_type, search, min_confidence, limit)
        from sqlalchemy import text

        clauses = ["tenant_id = :tid"]
        params: dict[str, Any] = {"tid": tenant_id, "lim": limit}
        if node_type is not None:
            clauses.append("node_type = :ntype")
            params["ntype"] = node_type.value
        if search:
            # Substring semantics, case-insensitive — same as the in-memory path.
            # Served by the trigram indexes on label / content.
            clauses.append(
                "(label ILIKE :pat ESCAPE '\\' OR content ILIKE :pat ESCAPE '\\')"
            )
            params["pat"] = _like_pattern(search)
        if min_confidence > 0:
            clauses.append("confidence >= :minc")
            params["minc"] = float(min_confidence)
        async with self._session(tenant_id) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_NODE_COLS} FROM knowledge_nodes "
                        f"WHERE {' AND '.join(clauses)} "
                        "ORDER BY confidence DESC, label ASC LIMIT :lim"
                    ),
                    params,
                )
            ).fetchall()
        return [n for n in (_row_to_node(r) for r in rows) if n is not None]

    async def acount_nodes(self, tenant_id: str) -> int:
        if self._db is None:
            return len(self._tenant_nodes.get(tenant_id, set()))
        from sqlalchemy import text

        async with self._session(tenant_id) as session:
            return int(
                (
                    await session.execute(
                        text("SELECT count(*) FROM knowledge_nodes WHERE tenant_id = :tid"),
                        {"tid": tenant_id},
                    )
                ).scalar_one()
            )

    async def aget_edges_for_node(
        self, node_id: str, tenant_id: str, limit: int = _MAX_EDGES_PER_NODE
    ) -> list[GraphEdge]:
        """Edges incident to *node_id* (either direction), bounded."""
        limit = max(1, min(int(limit), _MAX_EDGES_PER_NODE))
        if self._db is None:
            return self._memory_edges_for_node(node_id, tenant_id)[:limit]
        from sqlalchemy import text

        async with self._session(tenant_id) as session:
            rows = (
                await session.execute(
                    text(
                        # Two index-driven arms rather than one OR, so each uses
                        # its (tenant_id, source|target) index.
                        f"(SELECT {_EDGE_COLS} FROM knowledge_edges "
                        " WHERE tenant_id = :tid AND source_node_id = :nid) "
                        "UNION "
                        f"(SELECT {_EDGE_COLS} FROM knowledge_edges "
                        " WHERE tenant_id = :tid AND target_node_id = :nid) "
                        "ORDER BY confidence DESC, id LIMIT :lim"
                    ),
                    {"tid": tenant_id, "nid": node_id, "lim": limit},
                )
            ).fetchall()
        return [e for e in (_row_to_edge(r) for r in rows) if e is not None]

    async def aget_neighbors(
        self, node_id: str, tenant_id: str, limit: int = _MAX_EDGES_PER_NODE
    ) -> list[dict[str, Any]]:
        """Undirected adjacency of *node_id*: ``target``, ``relation``, ``edge_id``,
        ``confidence`` per neighbour."""
        edges = await self.aget_edges_for_node(node_id, tenant_id, limit=limit)
        return self._neighbors_from_edges(node_id, edges)

    async def afind_path(
        self,
        source_id: str,
        target_id: str,
        tenant_id: str,
        max_hops: int = 3,
        max_paths: int = 5,
    ) -> list[list[str]]:
        """Directed BFS (source→target edges), up to *max_paths* paths.

        Level by level: each hop is one indexed query over the current frontier,
        so memory is bounded by what the search actually visits (and capped per
        level), never by the size of the graph.
        """
        if source_id == target_id:
            return [[source_id]]
        max_hops = max(1, min(int(max_hops), 8))
        if self._db is None:
            return self._memory_find_path(source_id, target_id, tenant_id, max_hops, max_paths)

        from sqlalchemy import text

        parent: dict[str, str | None] = {source_id: None}
        frontier: list[str] = [source_id]
        paths: list[list[str]] = []

        def _path_to(node: str) -> list[str]:
            out: list[str] = []
            cur: str | None = node
            while cur is not None:
                out.append(cur)
                cur = parent.get(cur)
            return out[::-1]

        async with self._session(tenant_id) as session:
            for _depth in range(max_hops):
                if not frontier or len(paths) >= max_paths:
                    break
                next_frontier: list[str] = []
                for batch in _chunks(frontier, _PATH_PROBE_CHUNK):
                    rows = (
                        await session.execute(
                            text(
                                "SELECT source_node_id, target_node_id FROM knowledge_edges "
                                "WHERE tenant_id = :tid AND source_node_id = ANY(:ids) "
                                "ORDER BY source_node_id, target_node_id"
                            ),
                            {"tid": tenant_id, "ids": list(batch)},
                        )
                    ).fetchall()
                    for u, v in rows:
                        u, v = str(u), str(v)
                        if v == target_id:
                            if len(paths) < max_paths:
                                paths.append([*_path_to(u), v])
                        elif v not in parent:
                            parent[v] = u
                            if len(next_frontier) < _PATH_FRONTIER_CAP:
                                next_frontier.append(v)
                frontier = next_frontier
        return paths

    async def aget_graph_stats(self, tenant_id: str) -> dict[str, Any]:
        if self._db is None:
            return self._memory_graph_stats(tenant_id)
        from sqlalchemy import text

        async with self._session(tenant_id) as session:
            type_rows = (
                await session.execute(
                    text(
                        "SELECT node_type, count(*), COALESCE(sum(confidence), 0) "
                        "FROM knowledge_nodes WHERE tenant_id = :tid GROUP BY node_type"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
            total_edges = int(
                (
                    await session.execute(
                        text("SELECT count(*) FROM knowledge_edges WHERE tenant_id = :tid"),
                        {"tid": tenant_id},
                    )
                ).scalar_one()
            )
        type_counts = {str(r[0]): int(r[1]) for r in type_rows}
        total_nodes = sum(type_counts.values())
        conf_sum = sum(float(r[2]) for r in type_rows)
        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "node_types": type_counts,
            "avg_confidence": conf_sum / max(total_nodes, 1),
        }

    async def adetect_communities(
        self, tenant_id: str, max_communities: int = _CC_MAX_COMMUNITIES
    ) -> list[dict[str, Any]]:
        """Connected components (undirected), computed inside Postgres.

        Label propagation over a transaction-scoped temp table: every node starts
        labelled with its own id and repeatedly takes the minimum label among its
        neighbours until nothing changes (≈ graph-diameter iterations, capped).
        The app only ever receives the top *max_communities* components by size,
        each with its central (highest-degree) node, density and up to 1000
        member ids — never the graph. Semantics match ``CommunityDetector``:
        singletons are skipped and edges to unknown nodes are ignored.
        """
        max_communities = max(1, min(int(max_communities), 1000))
        if self._db is None:
            return self._memory_detect_communities(tenant_id)[:max_communities]

        from sqlalchemy import text

        edge_pairs = (
            "SELECT e.source_node_id AS a, e.target_node_id AS b FROM knowledge_edges e "
            "WHERE e.tenant_id = :tid "
            "UNION ALL "
            "SELECT e.target_node_id, e.source_node_id FROM knowledge_edges e "
            "WHERE e.tenant_id = :tid"
        )
        async with self._session(tenant_id) as session:
            await session.execute(
                text(
                    "CREATE TEMP TABLE _kg_cc (node_id text PRIMARY KEY, label text NOT NULL) "
                    "ON COMMIT DROP"
                )
            )
            # Only nodes that touch an edge can join a component of size >= 2.
            await session.execute(
                text(
                    "INSERT INTO _kg_cc (node_id, label) "
                    "SELECT n.id, n.id FROM knowledge_nodes n "
                    "WHERE n.tenant_id = :tid AND EXISTS ("
                    "  SELECT 1 FROM knowledge_edges e WHERE e.tenant_id = :tid "
                    "  AND (e.source_node_id = n.id OR e.target_node_id = n.id))"
                ),
                {"tid": tenant_id},
            )
            converged = False
            for _ in range(_CC_MAX_ITERATIONS):
                changed = await session.execute(
                    text(
                        "UPDATE _kg_cc c SET label = s.min_label FROM ("
                        "  SELECT p.a AS node_id, min(nb.label) AS min_label "
                        f"  FROM ({edge_pairs}) p JOIN _kg_cc nb ON nb.node_id = p.b "
                        "  GROUP BY p.a"
                        ") s WHERE c.node_id = s.node_id AND s.min_label < c.label"
                    ),
                    {"tid": tenant_id},
                )
                if not changed.rowcount:
                    converged = True
                    break
            if not converged:
                _log.warning(
                    "kg_communities_not_converged tenant=%s iterations=%d",
                    tenant_id,
                    _CC_MAX_ITERATIONS,
                )

            rows = (
                await session.execute(
                    text(
                        "WITH deg AS ("
                        "  SELECT p.a AS node_id, count(*) AS d "
                        f"  FROM ({edge_pairs}) p "
                        "  JOIN _kg_cc x ON x.node_id = p.a JOIN _kg_cc y ON y.node_id = p.b "
                        "  GROUP BY p.a"
                        "), comp AS ("
                        "  SELECT label AS root, count(*) AS size FROM _kg_cc "
                        "  GROUP BY label HAVING count(*) >= 2 "
                        "  ORDER BY count(*) DESC, label LIMIT :k"
                        "), intra AS ("
                        "  SELECT c.label AS root, count(*) AS edges_in "
                        "  FROM knowledge_edges e "
                        "  JOIN _kg_cc c ON c.node_id = e.source_node_id "
                        "  JOIN _kg_cc t ON t.node_id = e.target_node_id "
                        "  WHERE e.tenant_id = :tid GROUP BY c.label"
                        "), central AS ("
                        "  SELECT DISTINCT ON (c.label) c.label AS root, c.node_id "
                        "  FROM _kg_cc c LEFT JOIN deg ON deg.node_id = c.node_id "
                        "  WHERE c.label IN (SELECT root FROM comp) "
                        "  ORDER BY c.label, COALESCE(deg.d, 0) DESC, c.node_id"
                        ") "
                        "SELECT comp.root, comp.size, COALESCE(intra.edges_in, 0), "
                        "       central.node_id, n.label, "
                        "       (SELECT array_agg(m.node_id ORDER BY m.node_id) FROM ("
                        "          SELECT node_id FROM _kg_cc WHERE label = comp.root "
                        "          ORDER BY node_id LIMIT :members) m) "
                        "FROM comp "
                        "LEFT JOIN intra ON intra.root = comp.root "
                        "LEFT JOIN central ON central.root = comp.root "
                        "LEFT JOIN knowledge_nodes n "
                        "  ON n.id = central.node_id AND n.tenant_id = :tid "
                        "ORDER BY comp.size DESC, comp.root"
                    ),
                    {"tid": tenant_id, "k": max_communities, "members": _CC_MAX_MEMBERS_LISTED},
                )
            ).fetchall()

        communities: list[dict[str, Any]] = []
        for root, size, edges_in, central, central_label, members in rows:
            n = int(size)
            possible = n * (n - 1) / 2.0
            member_ids = [str(m) for m in (members or [])]
            communities.append(
                {
                    "community_id": str(uuid.uuid5(_COMMUNITY_NS, f"{tenant_id}:{root}")),
                    "node_ids": member_ids,
                    "node_ids_truncated": n > len(member_ids),
                    "size": n,
                    "central_node": str(central) if central else member_ids[0],
                    "density": round(int(edges_in) / possible, 4) if possible else 0.0,
                    "tenant_id": tenant_id,
                    "name": f"Cluster: {central_label or 'Community'}",
                    "summary": f"Connected component with {n} nodes",
                }
            )
        return communities

    async def aexport(
        self,
        tenant_id: str,
        *,
        limit: int = 1000,
        node_cursor: str | None = None,
        edge_cursor: str | None = None,
    ) -> dict[str, Any]:
        """One keyset page of nodes and edges, plus cursors for the next page."""
        limit = max(1, min(int(limit), _MAX_EXPORT_PAGE))
        if self._db is None:
            nodes = sorted(self._memory_nodes(tenant_id), key=lambda n: n.node_id)
            edges = sorted(self._memory_edges(tenant_id), key=lambda e: e.edge_id)
            nodes = [n for n in nodes if node_cursor is None or n.node_id > node_cursor][:limit]
            edges = [e for e in edges if edge_cursor is None or e.edge_id > edge_cursor][:limit]
        else:
            from sqlalchemy import text

            async with self._session(tenant_id) as session:
                node_rows = (
                    await session.execute(
                        text(
                            f"SELECT {_NODE_COLS} FROM knowledge_nodes WHERE tenant_id = :tid "
                            "AND (CAST(:cur AS text) IS NULL OR id > CAST(:cur AS text)) "
                            "ORDER BY id LIMIT :lim"
                        ),
                        {"tid": tenant_id, "cur": node_cursor, "lim": limit},
                    )
                ).fetchall()
                edge_rows = (
                    await session.execute(
                        text(
                            f"SELECT {_EDGE_COLS} FROM knowledge_edges WHERE tenant_id = :tid "
                            "AND (CAST(:cur AS text) IS NULL OR id > CAST(:cur AS text)) "
                            "ORDER BY id LIMIT :lim"
                        ),
                        {"tid": tenant_id, "cur": edge_cursor, "lim": limit},
                    )
                ).fetchall()
            nodes = [n for n in (_row_to_node(r) for r in node_rows) if n is not None]
            edges = [e for e in (_row_to_edge(r) for r in edge_rows) if e is not None]
        return {
            "nodes": nodes,
            "edges": edges,
            "next_node_cursor": nodes[-1].node_id if len(nodes) == limit else None,
            "next_edge_cursor": edges[-1].edge_id if len(edges) == limit else None,
        }

    # ------------------------------------------------------------------
    # Sync reads — in-memory (dev) API only
    # ------------------------------------------------------------------

    def get_node(self, node_id: str, tenant_id: str) -> GraphNode | None:
        self._require_memory_mode("get_node")
        node = self._nodes.get(node_id)
        return node if node and node.tenant_id == tenant_id else None

    def query_nodes(
        self,
        tenant_id: str,
        node_type: NodeType | None = None,
        search: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[GraphNode]:
        self._require_memory_mode("query_nodes")
        return self._memory_query_nodes(tenant_id, node_type, search, min_confidence, limit)

    def get_edges_for_node(self, node_id: str, tenant_id: str) -> list[GraphEdge]:
        self._require_memory_mode("get_edges_for_node")
        return self._memory_edges_for_node(node_id, tenant_id)

    def get_neighbors(self, node_id: str, tenant_id: str) -> list[dict[str, Any]]:
        self._require_memory_mode("get_neighbors")
        return self._neighbors_from_edges(node_id, self._memory_edges_for_node(node_id, tenant_id))

    def find_path(
        self, source_id: str, target_id: str, tenant_id: str, max_hops: int = 3
    ) -> list[list[str]]:
        self._require_memory_mode("find_path")
        if source_id == target_id:
            return [[source_id]]
        return self._memory_find_path(source_id, target_id, tenant_id, max_hops, 5)

    def get_graph_stats(self, tenant_id: str) -> dict[str, Any]:
        self._require_memory_mode("get_graph_stats")
        return self._memory_graph_stats(tenant_id)

    def detect_communities(self, tenant_id: str) -> list[dict[str, Any]]:
        self._require_memory_mode("detect_communities")
        return self._memory_detect_communities(tenant_id)

    # ------------------------------------------------------------------
    # In-memory implementations
    # ------------------------------------------------------------------

    def _memory_query_nodes(
        self,
        tenant_id: str,
        node_type: NodeType | None,
        search: str | None,
        min_confidence: float,
        limit: int,
    ) -> list[GraphNode]:
        node_ids = self._tenant_nodes.get(tenant_id, set())
        nodes = [self._nodes[nid] for nid in node_ids if nid in self._nodes]
        if node_type:
            nodes = [n for n in nodes if n.node_type == node_type]
        if search:
            sq = search.lower()
            nodes = [n for n in nodes if sq in n.label.lower() or sq in n.content.lower()]
        if min_confidence > 0:
            nodes = [n for n in nodes if n.confidence >= min_confidence]
        nodes.sort(key=lambda n: (-n.confidence, n.label))
        return nodes[:limit]

    def _memory_nodes(self, tenant_id: str) -> list[GraphNode]:
        ids = self._tenant_nodes.get(tenant_id, set())
        return [self._nodes[i] for i in ids if i in self._nodes]

    def _memory_edges(self, tenant_id: str) -> list[GraphEdge]:
        ids = self._tenant_edges.get(tenant_id, set())
        return [self._edges[i] for i in ids if i in self._edges]

    def _memory_edges_for_node(self, node_id: str, tenant_id: str) -> list[GraphEdge]:
        edge_ids = self._tenant_edges.get(tenant_id, set())
        return [
            self._edges[eid]
            for eid in edge_ids
            if eid in self._edges
            and (
                self._edges[eid].source_node_id == node_id
                or self._edges[eid].target_node_id == node_id
            )
        ]

    @staticmethod
    def _neighbors_from_edges(node_id: str, edges: Iterable[GraphEdge]) -> list[dict[str, Any]]:
        neighbors: list[dict[str, Any]] = []
        for edge in edges:
            if edge.source_node_id == node_id:
                other = edge.target_node_id
            elif edge.target_node_id == node_id:
                other = edge.source_node_id
            else:  # pragma: no cover - only incident edges are passed in
                continue
            neighbors.append(
                {
                    "target": other,
                    "relation": edge.edge_type.value,
                    "edge_id": edge.edge_id,
                    "confidence": edge.confidence,
                }
            )
        return neighbors

    def _memory_find_path(
        self, source_id: str, target_id: str, tenant_id: str, max_hops: int, max_paths: int
    ) -> list[list[str]]:
        adj: dict[str, list[str]] = {}
        for eid in self._tenant_edges.get(tenant_id, set()):
            e = self._edges.get(eid)
            if e:
                adj.setdefault(e.source_node_id, []).append(e.target_node_id)
        queue: deque[list[str]] = deque([[source_id]])
        visited = {source_id}
        paths: list[list[str]] = []
        while queue and len(paths) < max_paths:
            path = queue.popleft()
            if len(path) > max_hops:
                break
            for neighbor in sorted(adj.get(path[-1], [])):
                if neighbor == target_id:
                    if len(paths) < max_paths:
                        paths.append([*path, neighbor])
                elif neighbor not in visited:
                    visited.add(neighbor)
                    queue.append([*path, neighbor])
        return paths

    def _memory_graph_stats(self, tenant_id: str) -> dict[str, Any]:
        nodes = self._memory_nodes(tenant_id)
        type_counts: dict[str, int] = {}
        for n in nodes:
            type_counts[n.node_type.value] = type_counts.get(n.node_type.value, 0) + 1
        return {
            "total_nodes": len(nodes),
            "total_edges": len(self._tenant_edges.get(tenant_id, set())),
            "node_types": type_counts,
            "avg_confidence": sum(n.confidence for n in nodes) / max(len(nodes), 1),
        }

    def _memory_detect_communities(self, tenant_id: str) -> list[dict[str, Any]]:
        from app.knowledge_graph.community_detection import CommunityDetector

        nodes = self._memory_nodes(tenant_id)
        edges = self._memory_edges(tenant_id)
        communities = []
        for c in CommunityDetector().detect_communities(nodes, edges):
            central = c.get("central_node", "")
            label = self._nodes[central].label if central in self._nodes else "Community"
            communities.append(
                {
                    **c,
                    "node_ids_truncated": False,
                    "tenant_id": tenant_id,
                    "name": f"Cluster: {label}",
                    "summary": f"Connected component with {c['size']} nodes",
                }
            )
        communities.sort(key=lambda c: (-int(c["size"]), str(c["central_node"])))
        return communities


# Module-level singleton
kg_store = KnowledgeGraphStore()
