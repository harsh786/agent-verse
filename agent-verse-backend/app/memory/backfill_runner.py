"""Bounded, resumable backfill of legacy Reflexion lessons into canonical memory.

``ReflexionWirer``/``ReflexionStore`` persisted free-text failure lessons to the
legacy ``reflexion_lessons`` table, which the canonical ``ReflexionService``
never reads. This runner copies them, one tenant at a time, through the
canonical write path (:func:`app.memory.backfill.backfill_memory_rows` →
repository ``write``) so they get evidence refs, retention, RLS and
quarantine rules like every other memory.

* **Bounded** — at most ``max_rows`` legacy rows per invocation, fetched in
  ``batch_size`` pages (both clamped).
* **Resumable** — a keyset checkpoint (last processed ``id``) is committed to
  ``memory_backfill_checkpoints`` after every page; the next run continues from
  it. Writes are idempotent (``backfill:<table>:<id>`` keys), so a crash
  between a page's writes and its checkpoint only re-writes that page, and
  ``reset=True`` safely re-scans from the start (e.g. to pick up lessons the
  legacy writer added behind the checkpoint).
* **Governed** — every lesson is screened by the MEMORY_WRITE guardrail first
  (blocked → skipped; un-vettable → the run stops fail-closed without moving
  the checkpoint past it), and all reads/writes run under the tenant's RLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.memory.backfill import BackfillCheckpoint, LegacyMemoryRow, backfill_memory_rows
from app.memory.screening import screen_memory_content
from app.observability.logging import get_logger

LEGACY_REFLEXION_TABLE = "reflexion_lessons"
MAX_BATCH_SIZE = 500
MAX_ROWS_PER_RUN = 10_000

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillRunResult:
    tenant_id: str
    source_table: str
    processed: int
    written: int
    skipped: int
    completed: bool
    last_source_id: str | None


async def _load_checkpoint(db_factory: Any, tenant_id: str) -> tuple[str | None, int]:
    async with db_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        row = (
            await s.execute(
                text(
                    "SELECT last_source_id, rows_processed FROM memory_backfill_checkpoints "
                    "WHERE tenant_id = :t AND source_table = :s"
                ),
                {"t": tenant_id, "s": LEGACY_REFLEXION_TABLE},
            )
        ).first()
    if row is None:
        return None, 0
    return (str(row[0]) if row[0] is not None else None), int(row[1] or 0)


async def _save_checkpoint(
    db_factory: Any,
    tenant_id: str,
    *,
    last_source_id: str | None,
    last_created_at: datetime | None,
    rows_processed: int,
    completed: bool,
) -> None:
    async with db_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        await s.execute(
            text(
                "INSERT INTO memory_backfill_checkpoints "
                "(tenant_id, source_table, last_source_id, last_created_at, rows_processed, "
                " completed, updated_at) "
                "VALUES (:t, :s, :i, :c, :n, :done, :now) "
                "ON CONFLICT (tenant_id, source_table) DO UPDATE SET "
                "last_source_id = EXCLUDED.last_source_id, "
                "last_created_at = EXCLUDED.last_created_at, "
                "rows_processed = EXCLUDED.rows_processed, "
                "completed = EXCLUDED.completed, updated_at = EXCLUDED.updated_at"
            ),
            {
                "t": tenant_id,
                "s": LEGACY_REFLEXION_TABLE,
                "i": last_source_id,
                "c": last_created_at,
                "n": rows_processed,
                "done": completed,
                "now": datetime.now(UTC),
            },
        )


async def _fetch_page(
    db_factory: Any, tenant_id: str, *, after_id: str | None, limit: int
) -> list[Any]:
    async with db_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        result = await s.execute(
            text(
                "SELECT id, lesson, source_goal_id, failure_class, created_at "
                "FROM reflexion_lessons "
                "WHERE tenant_id = :t AND (CAST(:after AS VARCHAR) IS NULL OR id > :after) "
                "ORDER BY id LIMIT :n"
            ),
            {"t": tenant_id, "after": after_id, "n": limit},
        )
        return list(result.all())


async def run_reflexion_lessons_backfill(
    db_factory: Any,
    repository: Any,
    *,
    tenant_id: str,
    batch_size: int = 100,
    max_rows: int = 1_000,
    reset: bool = False,
) -> BackfillRunResult:
    """Backfill up to ``max_rows`` of a tenant's legacy lessons; resumable."""
    batch = max(1, min(MAX_BATCH_SIZE, int(batch_size)))
    budget = max(1, min(MAX_ROWS_PER_RUN, int(max_rows)))
    after_id, total = (None, 0) if reset else await _load_checkpoint(db_factory, tenant_id)
    processed = written = skipped = 0
    completed = False

    async def _no_checkpoint(checkpoint: BackfillCheckpoint) -> None:
        return None  # the page checkpoint below carries the keyset position

    while processed < budget:
        requested = min(batch, budget - processed)
        page = await _fetch_page(db_factory, tenant_id, after_id=after_id, limit=requested)
        if not page:
            completed = True
            await _save_checkpoint(
                db_factory,
                tenant_id,
                last_source_id=after_id,
                last_created_at=None,
                rows_processed=total,
                completed=True,
            )
            break
        rows: list[LegacyMemoryRow] = []
        for legacy_id, lesson, source_goal_id, failure_class, _created in page:
            screened = await screen_memory_content(
                str(lesson or ""), tenant_id=tenant_id, goal_id=str(source_goal_id or "")
            )
            if not screened or not screened.strip():
                skipped += 1
                continue
            goal_ref = f"goal://{source_goal_id}" if source_goal_id else None
            rows.append(
                LegacyMemoryRow(
                    tenant_id=tenant_id,
                    source_table=LEGACY_REFLEXION_TABLE,
                    source_id=str(legacy_id),
                    memory_kind="reflexion",
                    content=f"{screened} (failure class: {failure_class or 'unknown'})",
                    source_goal_id=str(source_goal_id or "")[:32],
                    source_execution_id="legacy-backfill",
                    evidence_refs=(
                        f"legacy://{LEGACY_REFLEXION_TABLE}/{legacy_id}",
                        *((goal_ref,) if goal_ref else ()),
                    ),
                    classification="internal",
                    confidence=5_000,
                )
            )
        async for _ in backfill_memory_rows(
            repository, rows, checkpoint=_no_checkpoint, batch_size=max(1, len(rows))
        ):
            pass
        written += len(rows)
        processed += len(page)
        last = page[-1]
        after_id = str(last[0])
        total += len(page)
        completed = len(page) < requested
        await _save_checkpoint(
            db_factory,
            tenant_id,
            last_source_id=after_id,
            last_created_at=last[4],
            rows_processed=total,
            completed=completed,
        )
        if completed:
            break
    result = BackfillRunResult(
        tenant_id=tenant_id,
        source_table=LEGACY_REFLEXION_TABLE,
        processed=processed,
        written=written,
        skipped=skipped,
        completed=completed,
        last_source_id=after_id,
    )
    _log.info(
        "memory_backfill_run",
        tenant_id=tenant_id,
        processed=processed,
        written=written,
        skipped=skipped,
        completed=completed,
    )
    return result


__all__ = [
    "LEGACY_REFLEXION_TABLE",
    "BackfillRunResult",
    "run_reflexion_lessons_backfill",
]
