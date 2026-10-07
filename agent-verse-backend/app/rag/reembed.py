"""Re-embed one knowledge collection with the configured embedder (KB-25).

``re_embed_collection`` (Celery, maintenance queue) had no API or beat caller and
embedded through the global ``embedding_router`` whose provider is never set in
the worker, so a deployment that changed embedders could not re-embed existing
collections. It also refused any model whose dimension differed from the
collection's — which is exactly what changing embedders usually means.

This module holds the job:

* The embedder is the one the collection is (re-)bound to: the deployment
  default, or the model named by the re-embed request
  (``app.rag.collection_embedders``). The final transaction re-binds the
  collection (``embedding_provider`` / ``embedding_model`` / ``embedding_dim``)
  together with the table flip, so queries and ingestion switch to the new model
  exactly when its vectors become the ones served.
* Same dimension: vectors are rewritten in place, batch by batch.
* New dimension: rows are copied, batch by batch, into ``knowledge_chunks_<new>``
  (same ids, every other column carried over). The collection keeps serving from
  its old table until a final transaction — holding the collection row lock that
  ingestion also takes — catches up rows added or removed meanwhile, deletes the
  old rows and flips ``knowledge_collections.embedding_dim``. An interrupted run
  leaves the collection intact on its old dimension and can simply be re-run.
* Every statement runs under the tenant's RLS context with explicit tenant and
  collection predicates.
* Progress (for ``GET /knowledge/collections/{id}/re-embed``) lives in Redis so
  every API replica sees the worker's progress; a Redis-held lock keeps one run
  per collection at a time.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS, _chunk_table

logger = get_logger(__name__)

BATCH_SIZE = 50
LOCK_TTL_SECONDS = 6 * 3600
PROGRESS_TTL_SECONDS = 7 * 24 * 3600

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

_RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class ReembedError(RuntimeError):
    """The re-embed cannot run (or was refused) — nothing inconsistent was written."""


def progress_key(tenant_id: str, collection_id: str) -> str:
    return f"knowledge:reembed:progress:{tenant_id}:{collection_id}"


def lock_key(tenant_id: str, collection_id: str) -> str:
    return f"knowledge:reembed:lock:{tenant_id}:{collection_id}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8")
    return str(value)


async def read_progress(redis: Any, tenant_id: str, collection_id: str) -> dict[str, Any] | None:
    """The latest re-embed progress record for one collection, or None."""
    raw = _as_str(await redis.get(progress_key(tenant_id, collection_id)))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def acquire_lock(redis: Any, tenant_id: str, collection_id: str, job_id: str) -> bool:
    """One re-embed per collection at a time (SET NX with a safety TTL)."""
    return bool(
        await redis.set(lock_key(tenant_id, collection_id), job_id, nx=True, ex=LOCK_TTL_SECONDS)
    )


async def release_lock(redis: Any, tenant_id: str, collection_id: str, job_id: str) -> None:
    """Release the lock only if ``job_id`` still owns it."""
    await redis.eval(_RELEASE_LOCK_LUA, 1, lock_key(tenant_id, collection_id), job_id)


class ReembedProgress:
    """Best-effort progress writer: a Redis outage never breaks the re-embed."""

    def __init__(self, redis: Any, tenant_id: str, collection_id: str, job_id: str) -> None:
        self._redis = redis
        self._tenant_id = tenant_id
        self._collection_id = collection_id
        self.state: dict[str, Any] = {
            "job_id": job_id,
            "collection_id": collection_id,
            "status": "queued",
            "processed": 0,
            "total": None,
            "updated_at": _now(),
        }

    async def update(self, **fields: Any) -> None:
        self.state.update(fields)
        self.state["updated_at"] = _now()
        if self._redis is None:
            return
        try:
            await self._redis.set(
                progress_key(self._tenant_id, self._collection_id),
                json.dumps(self.state, default=str),
                ex=PROGRESS_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "reembed_progress_write_failed",
                collection_id=self._collection_id,
                error=str(exc)[:200],
            )


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(v):.9g}" for v in vec) + "]"


async def _embed_checked(embed: EmbedFn, texts: list[str]) -> list[list[float]]:
    vectors = await embed(texts)
    if len(vectors) != len(texts):
        raise ReembedError(f"embedder returned {len(vectors)} vectors for {len(texts)} chunks")
    if any(not vec for vec in vectors):
        raise ReembedError("embedder returned an empty vector; refusing to write it")
    return [list(vec) for vec in vectors]


async def _copy_columns(session: Any, source: str, target: str) -> list[str]:
    """Columns present (and writable) on both chunk tables, minus ``embedding``."""
    rows = (
        await session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name IN (:a, :b) "
                "AND is_generated = 'NEVER' AND column_name <> 'embedding'"
            ),
            {"a": source, "b": target},
        )
    ).fetchall()
    by_table: dict[str, list[str]] = {source: [], target: []}
    for table_name, column_name in rows:
        by_table.setdefault(str(table_name), []).append(str(column_name))
    target_columns = set(by_table[target])
    columns = sorted(c for c in by_table[source] if c in target_columns)
    if "id" not in columns:
        raise ReembedError(f"cannot copy chunks from {source} to {target}: no shared id column")
    return columns


async def re_embed_collection(
    *,
    db: Any,
    embed: EmbedFn,
    model_key: str,
    tenant_id: str,
    collection_id: str,
    progress: ReembedProgress | None = None,
    batch_size: int = BATCH_SIZE,
    bind_provider: str | None = None,
    bind_model: str | None = None,
    expected_dim: int | None = None,
) -> dict[str, Any]:
    """Re-embed every chunk of one collection with ``embed``.

    Returns ``{"collection_id", "re_embedded", "model", "dimension",
    "previous_dimension"}``. Raises :class:`ReembedError` (nothing half-applied)
    when the collection is unknown to this tenant or the embedder's output
    cannot be stored.
    """
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        crow = (
            await session.execute(
                text(
                    "SELECT embedding_dim FROM knowledge_collections "
                    "WHERE id = :cid AND tenant_id = :tid AND is_active IS TRUE"
                ),
                {"cid": collection_id, "tid": tenant_id},
            )
        ).fetchone()
        if crow is None:
            raise ReembedError(f"collection {collection_id} not found")
        old_dim = int(crow[0])
        old_table = _chunk_table(old_dim)
        total = int(
            (
                await session.execute(
                    text(
                        f"SELECT count(*) FROM {old_table} "
                        "WHERE collection_id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).scalar_one()
        )
    if progress is not None:
        await progress.update(
            status="running", total=total, processed=0, started_at=_now(), model=model_key
        )

    new_dim: int | None = None
    new_table = old_table
    columns: list[str] = []
    count = 0
    cursor: str | None = None
    while True:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            rows = (
                await session.execute(
                    text(
                        f"SELECT id, content FROM {old_table} "
                        "WHERE collection_id = :cid AND tenant_id = :tid "
                        "  AND (CAST(:after AS text) IS NULL OR id > CAST(:after AS text)) "
                        "ORDER BY id LIMIT :lim"
                    ),
                    {"cid": collection_id, "tid": tenant_id, "after": cursor, "lim": batch_size},
                )
            ).fetchall()
            if not rows:
                break
            vectors = await _embed_checked(embed, [str(row[1] or "") for row in rows])
            if new_dim is None:
                new_dim = len(vectors[0])
                _check_expected(model_key, new_dim, expected_dim)
                if new_dim not in SUPPORTED_EMBEDDING_DIMENSIONS:
                    raise ReembedError(
                        f"model {model_key} produces {new_dim}-dim vectors; supported "
                        f"dimensions: {', '.join(str(d) for d in SUPPORTED_EMBEDDING_DIMENSIONS)}"
                    )
                new_table = _chunk_table(new_dim)
                if new_table != old_table:
                    columns = await _copy_columns(session, old_table, new_table)
            await _write_batch(
                session,
                rows=rows,
                vectors=vectors,
                dim=new_dim,
                old_table=old_table,
                new_table=new_table,
                columns=columns,
                tenant_id=tenant_id,
                collection_id=collection_id,
            )
            count += len(rows)
            cursor = str(rows[-1][0])
        if progress is not None:
            await progress.update(processed=count)

    if new_dim is None:
        # An empty collection still has to move to the embedder's width, or the
        # next ingest into it would be refused.
        new_dim = len((await _embed_checked(embed, ["dimension probe"]))[0])
        _check_expected(model_key, new_dim, expected_dim)
        if new_dim not in SUPPORTED_EMBEDDING_DIMENSIONS:
            raise ReembedError(f"model {model_key} produces unsupported {new_dim}-dim vectors")
        new_table = _chunk_table(new_dim)
    final_dim = new_dim
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        # The same row lock ingestion holds for its whole transaction: no chunk
        # can be added to or removed from this collection while we finish.
        locked = (
            await session.execute(
                text(
                    "SELECT embedding_dim FROM knowledge_collections "
                    "WHERE id = :cid AND tenant_id = :tid FOR UPDATE"
                ),
                {"cid": collection_id, "tid": tenant_id},
            )
        ).fetchone()
        if locked is None or int(locked[0]) != old_dim:
            raise ReembedError(
                f"collection {collection_id} changed while it was being re-embedded; re-run it"
            )
        if new_table != old_table:
            if not columns:
                columns = await _copy_columns(session, old_table, new_table)
            count += await _finish_dimension_move(
                session,
                embed=embed,
                dim=final_dim,
                old_table=old_table,
                new_table=new_table,
                columns=columns,
                tenant_id=tenant_id,
                collection_id=collection_id,
            )
        if count or new_table != old_table or bind_model:
            # Record the model (and width) the vectors were actually produced
            # with, so ReembeddingPolicy.should_reembed comparisons are meaningful,
            # and bind the collection to it (queries / ingestion switch now).
            await session.execute(
                text(
                    "UPDATE knowledge_collections SET embedder = :m, embedding_dim = :dim, "
                    "embedding_provider = CASE WHEN CAST(:bm AS text) IS NULL "
                    "THEN embedding_provider ELSE CAST(:bp AS text) END, "
                    "embedding_model = COALESCE(CAST(:bm AS text), embedding_model), "
                    "updated_at = now() WHERE id = :cid AND tenant_id = :tid"
                ),
                {
                    "m": bind_model or model_key,
                    "dim": final_dim,
                    "bp": bind_provider,
                    "bm": bind_model,
                    "cid": collection_id,
                    "tid": tenant_id,
                },
            )
    return {
        "collection_id": collection_id,
        "re_embedded": count,
        "model": model_key,
        "dimension": final_dim,
        "previous_dimension": old_dim,
    }


def _check_expected(model_key: str, dimension: int, expected: int | None) -> None:
    if expected and dimension != expected:
        raise ReembedError(
            f"model {model_key} produced {dimension}-dim vectors but the collection is being "
            f"bound to it at {expected} dimensions; nothing was changed"
        )


async def _write_batch(
    session: Any,
    *,
    rows: Sequence[Any],
    vectors: list[list[float]],
    dim: int,
    old_table: str,
    new_table: str,
    columns: list[str],
    tenant_id: str,
    collection_id: str,
) -> None:
    for row, vec in zip(rows, vectors, strict=True):
        if len(vec) != dim:
            raise ReembedError(
                f"embedder returned vectors of mixed width ({len(vec)} and {dim})"
            )
        params = {
            "vec": _vector_literal(vec),
            "id": row[0],
            "cid": collection_id,
            "tid": tenant_id,
        }
        if new_table == old_table:
            await session.execute(
                text(
                    f"UPDATE {old_table} SET embedding = CAST(:vec AS vector) "
                    "WHERE id = :id AND collection_id = :cid AND tenant_id = :tid"
                ),
                params,
            )
            continue
        column_list = ", ".join(columns)
        # Idempotent: a re-run after an interrupted move overwrites the vector.
        await session.execute(
            text(
                f"INSERT INTO {new_table} ({column_list}, embedding) "
                f"SELECT {column_list}, CAST(:vec AS vector) FROM {old_table} "
                "WHERE id = :id AND collection_id = :cid AND tenant_id = :tid "
                "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding"
            ),
            params,
        )


async def _finish_dimension_move(
    session: Any,
    *,
    embed: EmbedFn,
    dim: int,
    old_table: str,
    new_table: str,
    columns: list[str],
    tenant_id: str,
    collection_id: str,
) -> int:
    """Under the collection lock: reconcile, drop the old rows. Returns catch-up count."""
    scope = {"cid": collection_id, "tid": tenant_id}
    # Rows deleted from the old table after they were copied.
    await session.execute(
        text(
            f"DELETE FROM {new_table} n WHERE n.collection_id = :cid AND n.tenant_id = :tid "
            f"AND NOT EXISTS (SELECT 1 FROM {old_table} o WHERE o.id = n.id "
            "AND o.collection_id = :cid AND o.tenant_id = :tid)"
        ),
        scope,
    )
    # Rows added to the old table after the copy pass went by.
    missing = (
        await session.execute(
            text(
                f"SELECT o.id, o.content FROM {old_table} o "
                "WHERE o.collection_id = :cid AND o.tenant_id = :tid "
                f"AND NOT EXISTS (SELECT 1 FROM {new_table} n WHERE n.id = o.id "
                "AND n.collection_id = :cid AND n.tenant_id = :tid) ORDER BY o.id"
            ),
            scope,
        )
    ).fetchall()
    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start : start + BATCH_SIZE]
        vectors = await _embed_checked(embed, [str(row[1] or "") for row in batch])
        await _write_batch(
            session,
            rows=batch,
            vectors=vectors,
            dim=dim,
            old_table=old_table,
            new_table=new_table,
            columns=columns,
            tenant_id=tenant_id,
            collection_id=collection_id,
        )
    await session.execute(
        text(f"DELETE FROM {old_table} WHERE collection_id = :cid AND tenant_id = :tid"),
        scope,
    )
    return len(missing)
