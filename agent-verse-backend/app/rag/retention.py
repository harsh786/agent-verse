"""Expiry of real knowledge chunks (``knowledge_chunks_<dim>``) past ``expires_at``.

Retention (``expire_stale_documents``) only ever deleted the legacy ``documents``
table, which ``KnowledgeStore`` never writes. The chunks that actually back
retrieval carry their own deadline — ``expires_at``, set at ingest from the
Source's freshness TTL — and retrieval already hides a chunk once it passes, but
nothing ever deleted one. So expired knowledge accumulated forever (storage,
index size, HNSW build cost, the tenant's size quota), along with the knowledge
graph extracted from it.

Shape, for a corpus of millions of documents:

* **Scan** on the maintenance (BYPASSRLS) factory — it is genuinely cross-tenant
  — one bounded batch at a time, served by the partial
  ``idx_knowledge_chunks_<dim>_expires`` index (``WHERE expires_at IS NOT
  NULL``), so the scan reads only TTL'd chunks, never the whole table.
* **Delete** per tenant on the application factory under that tenant's RLS
  context *and* an explicit ``tenant_id`` predicate — the same least-privilege
  path every other tenant write takes.
* Collection counters (``chunk_count`` / ``document_count`` /
  ``total_size_bytes``) are decremented exactly, as ``delete_document_async``
  does, and once a document has no chunks left the graph nodes extracted from it
  (``source_id = doc`` or ``doc:<chunk_idx>``, see ``KGIngestionHook``) and their
  edges are deleted with it.
* Work per run is capped (``max_batches``); the remainder is picked up next run.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.observability.logging import get_logger
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS, _chunk_table

_log = get_logger(__name__)

SCAN_BATCH = 5000  # expired chunk rows read per scan
MAX_BATCHES = 20  # scans per dimension per run

# A chunk under an in-force legal hold is never expired: a tenant-wide hold
# covers every chunk of the tenant, a resource hold covers its collection or
# document. Expiry used to delete held knowledge (and its graph) regardless.
_NOT_UNDER_LEGAL_HOLD = (
    "NOT EXISTS (SELECT 1 FROM legal_holds lh WHERE lh.tenant_id = {t}.tenant_id "
    "AND lh.status = 'active' AND (lh.expires_at IS NULL OR lh.expires_at > now()) "
    "AND (lh.resource_type = 'tenant' "
    "OR lh.resource_ids @> jsonb_build_array(CAST({t}.collection_id AS text)) "
    "OR lh.resource_ids @> jsonb_build_array(CAST({t}.document_id AS text))))"
)


async def expire_knowledge_chunks(
    *,
    system_db: Any,
    app_db: Any = None,
    scan_batch: int = SCAN_BATCH,
    max_batches: int = MAX_BATCHES,
) -> dict[str, int]:
    from sqlalchemy import text

    from app.db.rls import system_session

    totals = {
        "knowledge_chunks_expired": 0,
        "knowledge_documents_expired": 0,
        "graph_nodes_deleted": 0,
        "graph_edges_deleted": 0,
    }
    for dim in SUPPORTED_EMBEDDING_DIMENSIONS:
        table = _chunk_table(dim)
        for _ in range(max_batches):
            async with system_db() as session, session.begin(), system_session(session):
                rows = list(
                    (
                        await session.execute(
                            text(
                                "SELECT DISTINCT tenant_id, collection_id, document_id FROM ("
                                f"  SELECT tenant_id, collection_id, document_id FROM {table} c "
                                "  WHERE expires_at IS NOT NULL AND expires_at < now() "
                                f"  AND {_NOT_UNDER_LEGAL_HOLD.format(t='c')} "
                                "  LIMIT :lim"
                                ") AS due"
                            ),
                            {"lim": int(scan_batch)},
                        )
                    ).fetchall()
                )
            if not rows:
                break
            by_tenant: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for tenant_id, collection_id, document_id in rows:
                by_tenant[str(tenant_id)].append((str(collection_id), str(document_id)))
            batch_deleted = 0
            if app_db is None:
                # Resolved only once there is something to delete: the per-tenant
                # deletes run on the APPLICATION role under RLS, never on the
                # maintenance role, and a run with nothing expired touches no
                # application connection at all.
                from app.db.session import get_session_factory

                app_db = get_session_factory()
            for tenant_id, docs in by_tenant.items():
                counts = await _expire_tenant_documents(app_db, table, tenant_id, docs)
                batch_deleted += counts["knowledge_chunks_expired"]
                for key, value in counts.items():
                    totals[key] += value
            if batch_deleted == 0:
                break  # no progress (raced / nothing deletable) — never spin
    if any(totals.values()):
        _log.info("knowledge_chunks_expired", **totals)
    return totals


async def _expire_tenant_documents(
    app_db: Any, table: str, tenant_id: str, docs: list[tuple[str, str]]
) -> dict[str, int]:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    counts = {
        "knowledge_chunks_expired": 0,
        "knowledge_documents_expired": 0,
        "graph_nodes_deleted": 0,
        "graph_edges_deleted": 0,
    }
    async with app_db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        for collection_id, document_id in docs:
            params = {"tid": tenant_id, "cid": collection_id, "did": document_id}
            removed = (
                await session.execute(
                    text(
                        # Re-checked here: a hold placed after the scan still wins.
                        f"DELETE FROM {table} c "
                        "WHERE tenant_id = :tid AND collection_id = :cid "
                        "AND document_id = :did "
                        "AND expires_at IS NOT NULL AND expires_at < now() "
                        f"AND {_NOT_UNDER_LEGAL_HOLD.format(t='c')} "
                        "RETURNING octet_length(content), chunk_index, id"
                    ),
                    params,
                )
            ).fetchall()
            if not removed:
                continue  # raced with a re-ingest / another run
            remaining = (
                await session.execute(
                    text(
                        f"SELECT 1 FROM {table} WHERE tenant_id = :tid "
                        "AND collection_id = :cid AND document_id = :did LIMIT 1"
                    ),
                    params,
                )
            ).first()
            document_gone = remaining is None
            await session.execute(
                text(
                    "UPDATE knowledge_collections "
                    "SET chunk_count = GREATEST(chunk_count - :d_chunks, 0), "
                    "    document_count = GREATEST(document_count - :d_docs, 0), "
                    "    total_size_bytes = GREATEST(COALESCE(total_size_bytes, 0) - :d_bytes, 0), "
                    "    updated_at = now() "
                    "WHERE id = :cid AND tenant_id = :tid"
                ),
                {
                    **params,
                    "d_chunks": len(removed),
                    "d_docs": 1 if document_gone else 0,
                    "d_bytes": sum(int(r[0] or 0) for r in removed),
                },
            )
            counts["knowledge_chunks_expired"] += len(removed)
            if not document_gone:
                continue
            counts["knowledge_documents_expired"] += 1
            nodes, edges = await delete_document_graph(session, tenant_id, document_id, removed)
            counts["graph_nodes_deleted"] += nodes
            counts["graph_edges_deleted"] += edges
    return counts


async def delete_document_graph(
    session: Any, tenant_id: str, document_id: str, removed: list[Any]
) -> tuple[int, int]:
    """Delete the KG rows extracted from ``document_id``.

    ``removed`` rows are ``(bytes, chunk_index, chunk_id)``. ``KGIngestionHook``
    stamps nodes with the id of the chunk they came from; nodes written before
    that carry ``f"{doc}:{i}"``. Both are enumerable from the removed chunks — an
    ``= ANY`` lookup on ``(tenant_id, source_id)`` instead of a ``LIKE`` scan
    over the tenant's whole graph.
    """
    from sqlalchemy import text

    span = max([len(removed), *[int(r[1] or 0) + 1 for r in removed]])
    source_ids = [
        document_id,
        *[str(r[2]) for r in removed if len(r) > 2 and r[2]],
        *[f"{document_id}:{i}" for i in range(span)],
    ]
    node_ids = [
        row[0]
        for row in (
            await session.execute(
                text(
                    "SELECT id FROM knowledge_nodes "
                    "WHERE tenant_id = :tid AND source_id = ANY(:sids)"
                ),
                {"tid": tenant_id, "sids": source_ids},
            )
        ).fetchall()
    ]
    if not node_ids:
        return 0, 0
    edges = (
        await session.execute(
            text(
                "DELETE FROM knowledge_edges WHERE tenant_id = :tid "
                "AND (source_node_id = ANY(:ids) OR target_node_id = ANY(:ids))"
            ),
            {"tid": tenant_id, "ids": node_ids},
        )
    ).rowcount
    nodes = (
        await session.execute(
            text("DELETE FROM knowledge_nodes WHERE tenant_id = :tid AND id = ANY(:ids)"),
            {"tid": tenant_id, "ids": node_ids},
        )
    ).rowcount
    return int(nodes or 0), int(edges or 0)
