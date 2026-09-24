"""ReflexionStore — persistent failure lessons per tenant.

In-memory deque for hot path; async DB persistence via record_async()
to the `reflexion_lessons` table (migration 0087).
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

# Re-hydrate a tenant's recent-lessons cache from the DB at most this often.
# recall() only re-pulls from the DB when its LOCAL deque is empty for that
# tenant — so as soon as this replica records even one lesson locally, it never
# looks at the DB again for that tenant, and lessons another replica recorded
# (and persisted via record_async) never become visible here. The TTL bounds
# that divergence instead of letting it last for the process lifetime.
_REHYDRATE_INTERVAL_SECONDS = 30.0


class ReflexionStore:
    def __init__(self, max_per_tenant: int = 50, db_factory: Any = None) -> None:
        self._lessons: dict[str, deque[dict[str, Any]]] = {}
        self._max = max_per_tenant
        self._db_factory = db_factory
        self._hydrated_tenants: set[str] = set()
        self._hydrated_at: dict[str, float] = {}

    # ── Sync (in-memory) ──────────────────────────────────────────────────────

    def record(
        self,
        *,
        tenant_id: str,
        lesson: str,
        source_goal_id: str,
        failure_class: str,
    ) -> None:
        """Record lesson in-memory (always succeeds, no DB)."""
        if tenant_id not in self._lessons:
            self._lessons[tenant_id] = deque(maxlen=self._max)
        self._lessons[tenant_id].append(
            {
                "lesson": lesson,
                "source_goal_id": source_goal_id,
                "failure_class": failure_class,
            }
        )

    def recall(self, *, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Recall recent failure lessons. Triggers lazy DB hydration on first miss."""
        lessons = list(self._lessons.get(tenant_id, []))

        # Lazy DB hydration — gated by a TTL (not "ever hydrated"), so a lesson
        # recorded locally on this replica doesn't permanently hide lessons
        # another replica persisted to the DB for the same tenant.
        _now = time.monotonic()
        _last = self._hydrated_at.get(tenant_id)
        _due = _last is None or _now - _last >= _REHYDRATE_INTERVAL_SECONDS
        if _due and self._db_factory is not None:
            self._hydrated_tenants.add(tenant_id)  # legacy marker, kept for compatibility
            self._hydrated_at[tenant_id] = _now  # mark immediately to prevent re-entry
            try:
                import asyncio

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule hydration asynchronously (best-effort)
                    asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                        self.load_from_db(tenant_id=tenant_id, db_factory=self._db_factory)
                    )
                else:
                    # Sync context — load directly
                    loop.run_until_complete(
                        self.load_from_db(tenant_id=tenant_id, db_factory=self._db_factory)
                    )
                # Re-read after sync load
                lessons = list(self._lessons.get(tenant_id, []))
            except Exception:
                pass

        return lessons[-limit:]

    # ── Async (in-memory + DB) ────────────────────────────────────────────────

    async def record_async(
        self,
        *,
        tenant_id: str,
        lesson: str,
        source_goal_id: str,
        failure_class: str,
        db_factory: Any = None,
    ) -> None:
        """Record lesson in-memory AND persist to Postgres reflexion_lessons table."""
        # Always write to memory first
        self.record(
            tenant_id=tenant_id,
            lesson=lesson,
            source_goal_id=source_goal_id,
            failure_class=failure_class,
        )
        if db_factory is None:
            return
        try:
            from sqlalchemy import text

            lesson_id = uuid.uuid4().hex
            async with db_factory() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO reflexion_lessons
                            (id, tenant_id, lesson, source_goal_id, failure_class, created_at)
                        VALUES
                            (:id, :tenant_id, :lesson, :source_goal_id, :failure_class, NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": lesson_id,
                        "tenant_id": tenant_id,
                        "lesson": lesson,
                        "source_goal_id": source_goal_id,
                        "failure_class": failure_class,
                    },
                )
        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("reflexion_lesson_db_persist_failed", error=str(exc))
            except Exception:
                pass

    async def load_from_db(
        self,
        *,
        tenant_id: str,
        db_factory: Any,
        limit: int = 50,
    ) -> None:
        """Seed in-memory store from DB on startup (survives restart)."""
        if db_factory is None:
            return
        try:
            from sqlalchemy import text

            async with db_factory() as session:
                rows = (
                    await session.execute(
                        text("""
                            SELECT tenant_id, lesson, source_goal_id, failure_class
                            FROM reflexion_lessons
                            WHERE tenant_id = :tenant_id
                            ORDER BY created_at DESC
                            LIMIT :limit
                        """),
                        {"tenant_id": tenant_id, "limit": limit},
                    )
                ).fetchall()
                for row in reversed(rows):
                    self.record(
                        tenant_id=row[0],
                        lesson=row[1],
                        source_goal_id=row[2],
                        failure_class=row[3],
                    )
        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("reflexion_lesson_load_from_db_failed", error=str(exc))
            except Exception:
                pass
