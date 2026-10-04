"""Daily long-term-memory consolidation: dedup + retention, per tenant (MEM-17).

``long_term_memory`` is FORCE ROW LEVEL SECURITY. The old task ran two global
DELETEs on the application session with no tenant GUC: under the NOBYPASSRLS
app role they matched nothing and the task reported success; under a BYPASSRLS
DSN they deleted across every tenant with no legal-hold check, as one
whole-table sort + anti-join in a single transaction.

Shape now (same as knowledge-chunk expiry, ``app.rag.retention``):

* **Scan** on the maintenance (BYPASSRLS) factory under ``system_session``:
  which tenants have memories, which are under a tenant-wide legal hold, and
  each tenant's ``retention_days`` setting.
* **Delete** per tenant on the application factory under that tenant's RLS
  context, in bounded batches (``batch_size`` rows per transaction, at most
  ``max_batches`` per tenant per run; the rest is picked up next run). Rows
  named by an in-force resource hold are never deleted, and a tenant-wide hold
  skips the tenant entirely (re-checked inside every DELETE).
* Any error propagates — the Celery task fails instead of returning normally.
"""

from __future__ import annotations

import os
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

BATCH_SIZE = 500
MAX_BATCHES = 50

_IN_FORCE = "lh.status = 'active' AND (lh.expires_at IS NULL OR lh.expires_at > now())"

# Row is not under an in-force hold (tenant-wide or naming the memory id).
_NOT_HELD = (
    "NOT EXISTS (SELECT 1 FROM legal_holds lh WHERE lh.tenant_id = m.tenant_id "
    f"AND {_IN_FORCE} AND (lh.resource_type = 'tenant' "
    "OR lh.resource_ids @> jsonb_build_array(CAST(m.id AS text))))"
)

# MEM-47: duplicate groups are found ONCE per tenant over the
# ix_long_term_memory_tenant_content_md5 expression index; the old dedup re-ran
# a window sort over every memory of the tenant in each of up to 50 batches.
_DUP_GROUPS_SQL = """
SELECT md5(m.content) AS h
  FROM long_term_memory m
 WHERE m.tenant_id = :tid
 GROUP BY md5(m.content)
HAVING count(*) > 1
 ORDER BY 1
 LIMIT :max_groups
"""

# Deletes all but the newest copy within the given duplicate groups only (the
# window spans just those rows, found through the md5 index). Content is part
# of the partition so an md5 collision never merges different memories.
_DEDUP_SQL = f"""
DELETE FROM long_term_memory
 WHERE tenant_id = :tid
   AND id IN (
     SELECT id FROM (
       SELECT m.id, row_number() OVER (
                PARTITION BY md5(m.content), m.content ORDER BY m.created_at DESC, m.id
              ) AS rn
         FROM long_term_memory m
        WHERE m.tenant_id = :tid AND md5(m.content) = ANY(CAST(:hashes AS text[]))
     ) ranked
     JOIN long_term_memory m USING (id)
    WHERE ranked.rn > 1 AND {_NOT_HELD}
    LIMIT :lim
   )
"""

_EXPIRE_SQL = f"""
DELETE FROM long_term_memory
 WHERE tenant_id = :tid
   AND id IN (
     SELECT m.id FROM long_term_memory m
      WHERE m.tenant_id = :tid
        AND m.created_at < now() - (:days * INTERVAL '1 day')
        AND {_NOT_HELD}
      LIMIT :lim
   )
"""


def _default_retention_days() -> int:
    return int(os.getenv("DATA_RETENTION_DAYS", "90"))


async def _scan(system_db: Any) -> tuple[list[str], set[str], dict[str, int]]:
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        tenants = [
            str(r[0])
            for r in (
                # Tenants (PK) probed by the tenant_id index, not a scan of
                # every memory (MEM-47).
                await session.execute(
                    text(
                        "SELECT t.id FROM tenants t WHERE EXISTS "
                        "(SELECT 1 FROM long_term_memory m WHERE m.tenant_id = t.id) "
                        "ORDER BY t.id"
                    )
                )
            ).fetchall()
        ]
        held = {
            str(r[0])
            for r in (
                await session.execute(
                    text(
                        "SELECT DISTINCT lh.tenant_id FROM legal_holds lh "
                        f"WHERE lh.resource_type = 'tenant' AND {_IN_FORCE}"
                    )
                )
            ).fetchall()
        }
        retention: dict[str, int] = {}
        for tid, days in (
            await session.execute(
                text(
                    "SELECT tenant_id, settings->>'retention_days' FROM tenant_settings "
                    "WHERE settings ? 'retention_days'"
                )
            )
        ).fetchall():
            try:
                if int(days) > 0:
                    retention[str(tid)] = int(days)
            except (TypeError, ValueError):
                _log.warning("ltm_consolidation_bad_retention", tenant_id=str(tid))
    return tenants, held, retention


async def _batched(app_db: Any, sql: str, params: dict[str, Any], max_batches: int) -> int:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    total = 0
    for _ in range(max_batches):
        async with (
            app_db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, params["tid"]),
        ):
            deleted = int((await session.execute(text(sql), params)).rowcount or 0)
        total += deleted
        if deleted < params["lim"]:
            break
    return total


async def _dedup_tenant(app_db: Any, tid: str, batch_size: int, max_batches: int) -> int:
    """One grouping query, then DELETEs over chunks of duplicate groups. At most
    ``max_batches`` DELETE transactions of at most ``batch_size`` rows each."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with app_db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
        hashes = [
            str(r[0])
            for r in (
                await session.execute(
                    text(_DUP_GROUPS_SQL),
                    {"tid": tid, "max_groups": batch_size * max_batches},
                )
            ).fetchall()
        ]
    total = 0
    batches = 0
    for start in range(0, len(hashes), batch_size):
        chunk = hashes[start : start + batch_size]
        while batches < max_batches:
            batches += 1
            async with (
                app_db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tid),
            ):
                deleted = int(
                    (
                        await session.execute(
                            text(_DEDUP_SQL), {"tid": tid, "hashes": chunk, "lim": batch_size}
                        )
                    ).rowcount
                    or 0
                )
            total += deleted
            if deleted < batch_size:
                break
        if batches >= max_batches:
            break
    return total


async def consolidate_long_term_memory(
    *,
    system_db: Any,
    app_db: Any,
    batch_size: int = BATCH_SIZE,
    max_batches: int = MAX_BATCHES,
) -> dict[str, int]:
    """Dedup + expire every tenant's long-term memory. Raises on any DB error."""
    tenants, held, retention = await _scan(system_db)
    totals = {
        "tenants_scanned": len(tenants),
        "tenants_skipped_legal_hold": 0,
        "duplicates_removed": 0,
        "expired_removed": 0,
    }
    default_days = _default_retention_days()
    for tid in tenants:
        if tid in held:
            totals["tenants_skipped_legal_hold"] += 1
            continue
        lim = max(1, int(batch_size))
        totals["duplicates_removed"] += await _dedup_tenant(
            app_db, tid, lim, max(1, int(max_batches))
        )
        params: dict[str, Any] = {"tid": tid, "lim": lim}
        params["days"] = retention.get(tid, default_days)
        totals["expired_removed"] += await _batched(app_db, _EXPIRE_SQL, params, max_batches)
    _log.info("ltm_consolidation_done", **totals)
    return totals
