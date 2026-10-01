"""Execution memory — stores winning plans and failed approaches across runs.

Winning plans are fed back into the planner prompt to bias toward proven approaches.
Failed approaches are included as negative examples to avoid repeating mistakes.

Durable store: the ``execution_memory`` table (tenant RLS). ``record_async`` /
``record_failure_async`` write it and ``recall_async`` / ``recall_failures_async``
/ ``list_async`` read it. The in-process dicts are a bounded cache (at most
``MAX_ENTRIES_PER_TENANT`` entries per tenant, ``MAX_TENANTS`` tenants, LRU) and
the whole store only in the DB-less dev/test build.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

_log = get_logger(__name__)

#: Bounds of the in-process cache (MEM-08: it grew forever on the app singleton).
MAX_ENTRIES_PER_TENANT = 100
MAX_TENANTS = 1_000


class _BoundedTenantLists(OrderedDict[str, list[dict[str, object]]]):
    """tenant_id -> newest-last list, capped per tenant and LRU-capped in tenants."""

    def add(self, tenant_id: str, entry: dict[str, object]) -> None:
        bucket = self.setdefault(tenant_id, [])
        bucket.append(entry)
        if len(bucket) > MAX_ENTRIES_PER_TENANT:
            del bucket[: len(bucket) - MAX_ENTRIES_PER_TENANT]
        self.move_to_end(tenant_id)
        while len(self) > MAX_TENANTS:
            self.popitem(last=False)


class RecallResult(list[dict[str, Any]]):
    """Recall hits, plus whether the authoritative store could be read.

    A plain ``list`` for every existing caller. ``degraded=True`` means a DB is
    configured but the query failed: the result is deliberately EMPTY rather
    than this replica's process-local cache, which is a partial view that other
    replicas do not share (it used to be returned silently as if it were the
    tenant's history).
    """

    degraded: bool

    def __init__(self, items: list[dict[str, Any]] | None = None, *, degraded: bool = False):
        super().__init__(items or [])
        self.degraded = degraded


def _write_failed(op: str, tenant_id: str, goal_id: str, exc: BaseException) -> None:
    """MEM-07: a lost durable write is counted and logged with its goal."""
    from app.observability.metrics import record_memory_degraded

    record_memory_degraded("execution", "record")
    _log.warning(
        "execution_memory_db_write_failed",
        op=op,
        tenant_id=tenant_id,
        goal_id=goal_id,
        error=f"{type(exc).__name__}: {str(exc)[:200]}",
    )


#: Plan steps vetted (and stored) per record.
_MAX_PLAN_STEPS = 30


async def _screen_record(
    *, goal: str, texts: list[str], tenant_id: str, goal_id: str
) -> tuple[str, list[str]] | None:
    """MEM-68: vet a record's goal + texts through the shared memory-write gate.

    ``(goal, texts)`` to store (redacted where a rule redacted), or ``None``
    when blocked. Raises ``MemoryScreeningError`` when the gate cannot vet it.
    """
    from app.memory.screening import screen_memory_fields

    fields = {"goal": goal[:500], **{f"t{i}": t for i, t in enumerate(texts)}}
    screened = await screen_memory_fields(
        fields, tenant_id=tenant_id, goal_id=goal_id or None, store="execution"
    )
    if screened is None:
        return None
    return screened["goal"], [screened[f"t{i}"] for i in range(len(texts))]


class ExecutionMemory:
    """Per-tenant store of past executions (successful plans and failures)."""

    def __init__(self) -> None:
        # Key: tenant_id → list of memory records (bounded, see module docstring)
        self._plans: _BoundedTenantLists = _BoundedTenantLists()
        self._failures: _BoundedTenantLists = _BoundedTenantLists()
        # Flat execution log (the DB-less REST list)
        self._memories: _BoundedTenantLists = _BoundedTenantLists()

    def record(
        self,
        *,
        goal: str,
        plan: list[str],
        tenant_ctx: TenantContext,
    ) -> None:
        self._plans.add(tenant_ctx.tenant_id, {"goal": goal, "plan": plan})

    def recall(
        self,
        *,
        goal_hint: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
    ) -> list[dict[str, object]]:
        hint = goal_hint.lower()
        matches = [
            m for m in self._plans.get(tenant_ctx.tenant_id, []) if hint in str(m["goal"]).lower()
        ]
        return matches[:top_k]

    def record_failure(
        self,
        *,
        goal: str,
        failed_step: str,
        error: str,
        tenant_ctx: TenantContext,
    ) -> None:
        self._failures.add(
            tenant_ctx.tenant_id, {"goal": goal, "failed_step": failed_step, "error": error}
        )

    def recall_failures(
        self,
        *,
        goal_hint: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
    ) -> list[dict[str, object]]:
        hint = goal_hint.lower()
        matches = [
            m
            for m in self._failures.get(tenant_ctx.tenant_id, [])
            if hint in str(m["goal"]).lower()
        ]
        return matches[:top_k]

    async def record_async(
        self,
        *,
        goal: str,
        plan: list[str],
        success: bool,
        tenant_id: str,
        db: Any = None,
        goal_id: str = "",
    ) -> bool:
        """Record to both in-memory dict and PostgreSQL.

        Returns whether the durable write happened (always True without a DB).
        A DB failure is counted and logged and returns False — callers flag the
        goal as memory-degraded rather than claim the plan was remembered.

        The goal and every plan step pass the shared memory-write gate first
        (MEM-68): a blocked record is stored nowhere (returns True — a policy
        decision, not a loss); a gate outage stores nothing and returns False.

        Uses ``tenant_id`` (str) directly so callers don't need a TenantContext
        object.  Also updates ``_plans`` so that the synchronous ``recall()``
        method can still find newly-persisted entries in the same session.
        """
        from datetime import UTC, datetime

        from app.memory.screening import MemoryScreeningError

        try:
            vetted = await _screen_record(
                goal=goal,
                texts=[str(p) for p in plan[:_MAX_PLAN_STEPS]],
                tenant_id=tenant_id,
                goal_id=goal_id,
            )
        except MemoryScreeningError as exc:
            _write_failed("screen", tenant_id, goal_id, exc)
            return False
        if vetted is None:
            return True  # withheld by the memory-write guardrail: nothing to store
        goal, plan = vetted

        tid = tenant_id
        entry: dict[str, object] = {
            "goal_text": goal,
            "goal": goal,
            "plan": plan,
            "success": success,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._memories.add(tid, entry)
        # Also update _plans so sync recall() can find entries in the same session
        if success:
            self._plans.add(tid, {"goal": goal, "plan": plan})

        if db is None:
            return True
        try:
            import json
            import uuid

            from sqlalchemy import text

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""INSERT INTO execution_memory
                        (id, tenant_id, goal_text, plan, success, created_at)
                        VALUES (:id, :tid, :goal, :plan, :success, NOW())"""),
                    {
                        "id": uuid.uuid4().hex,
                        "tid": tid,
                        "goal": goal[:500],
                        "plan": json.dumps(plan),
                        "success": success,
                    },
                )
        except Exception as exc:
            _write_failed("record", tid, goal_id, exc)
            return False
        return True

    async def record_failure_async(
        self,
        *,
        goal: str,
        error: str,
        tenant_id: str,
        db: Any = None,
        goal_id: str = "",
    ) -> bool:
        """Persist failed attempt to DB for cross-session pattern learning.

        Returns whether the durable write happened (see :meth:`record_async`)."""
        from app.memory.screening import MemoryScreeningError

        try:
            vetted = await _screen_record(
                goal=goal, texts=[error[:500]], tenant_id=tenant_id, goal_id=goal_id
            )
        except MemoryScreeningError as exc:
            _write_failed("screen", tenant_id, goal_id, exc)
            return False
        if vetted is None:
            return True  # withheld by the memory-write guardrail: nothing to store
        goal, (error,) = vetted
        # In-memory record
        self._failures.add(tenant_id, {"goal": goal, "error": error})

        if db is None:
            return True
        try:
            import json
            import uuid

            from sqlalchemy import text

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO execution_memory
                            (id, tenant_id, goal_text, plan, success, created_at)
                        VALUES (:id, :tid, :goal, CAST(:plan AS jsonb), FALSE, NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "tid": tenant_id,
                        "goal": goal[:500],
                        "plan": json.dumps({"error": error[:500]}),
                    },
                )
        except Exception as exc:
            _write_failed("record_failure", tenant_id, goal_id, exc)
            return False
        return True

    async def list_async(
        self, *, tenant_id: str, db: Any = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """A tenant's recorded executions, newest first (the REST list, MEM-06).

        From ``execution_memory`` under the tenant's RLS when a DB is wired —
        the same on every replica and after a restart. Raises on a DB failure.
        Without a DB, this process's log is the store.
        """
        if db is None:
            rows = list(reversed(self._memories.get(tenant_id, [])))[offset : offset + limit]
            return [
                {
                    "goal_text": str(m.get("goal_text", ""))[:200],
                    "success": bool(m.get("success", False)),
                    "recorded_at": str(m.get("recorded_at", "")),
                }
                for m in rows
            ]
        from sqlalchemy import text

        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            db_rows = (
                await session.execute(
                    text(
                        "SELECT goal_text, success, created_at FROM execution_memory "
                        "WHERE tenant_id = :tid ORDER BY created_at DESC "
                        "LIMIT :lim OFFSET :off"
                    ),
                    {"tid": tenant_id, "lim": limit, "off": offset},
                )
            ).fetchall()
        return [
            {
                "goal_text": str(r[0] or "")[:200],
                "success": bool(r[1]),
                "recorded_at": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2] or ""),
            }
            for r in db_rows
        ]

    async def load_from_db(
        self,
        *,
        tenant_id: str,
        db: Any,
        limit: int = 100,
    ) -> int:
        """Seed in-memory _plans from DB on startup. Returns count loaded."""
        if db is None:
            return 0
        try:
            import json

            from sqlalchemy import text

            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                        SELECT tenant_id, goal_text, plan
                        FROM execution_memory
                        WHERE tenant_id = :tenant_id
                          AND success = TRUE
                        ORDER BY created_at DESC
                        LIMIT :limit
                    """),
                        {"tenant_id": tenant_id, "limit": limit},
                    )
                ).fetchall()
                count = 0
                for row in reversed(rows):  # oldest first so recent ones are at end
                    tid, goal, plan_json = str(row[0]), str(row[1]), row[2]
                    try:
                        plan = (
                            json.loads(plan_json)
                            if isinstance(plan_json, str)
                            else (plan_json or [])
                        )
                        self._plans.add(tid, {"goal": goal, "plan": plan})
                        count += 1
                    except Exception:
                        pass
                return count
        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("execution_memory_load_from_db_failed", error=str(exc))
            except Exception:
                pass
            return 0

    async def recall_failures_async(
        self,
        goal_hint: str,
        *,
        tenant_id: str,
        db: Any = None,
        limit: int = 3,
    ) -> RecallResult:
        """Recall relevant PAST FAILURES from DB for a given goal.

        ``record_failure_async`` persists failed attempts to ``execution_memory``
        with ``success = FALSE`` and the error stashed in the ``plan`` jsonb as
        ``{"error": ...}``. The synchronous ``recall_failures`` only reads the
        in-process ``self._failures`` dict, so a fresh worker/pod would replan
        blind — it never saw the failures earlier runs recorded. This DB-backed
        path lets the planner avoid repeating cross-session mistakes.

        Uses the in-memory ``self._failures`` only when ``db`` is None (no DB
        configured). A failed query logs a warning and returns an empty
        ``RecallResult(degraded=True)`` — never the replica-local cache.
        """

        def _from_memory() -> list[dict]:
            hint_lower = goal_hint.lower()
            words = hint_lower.split()[:5]
            results: list[dict] = []
            for m in self._failures.get(tenant_id, []):
                goal_str = str(m.get("goal", m.get("goal_text", "")))
                if not words or any(word in goal_str.lower() for word in words):
                    results.append(
                        {
                            "goal": goal_str,
                            "goal_text": goal_str,
                            "error": str(m.get("error", "")),
                        }
                    )
                    if len(results) >= limit:
                        break
            return results

        if db is None:
            return RecallResult(_from_memory())

        try:
            from sqlalchemy import text

            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                        SELECT goal_text, plan FROM execution_memory
                        WHERE tenant_id = :tid AND success = FALSE
                        ORDER BY created_at DESC LIMIT :lim
                    """),
                        {"tid": tenant_id, "lim": limit * 3},
                    )
                ).fetchall()

            hint_lower = goal_hint.lower()
            words = hint_lower.split()[:5]
            filtered: list[dict] = []
            for row in rows:
                goal_text, plan = row
                goal_str = str(goal_text or "")
                if words and not any(word in goal_str.lower() for word in words):
                    continue
                error = ""
                if isinstance(plan, dict):
                    error = str(plan.get("error", ""))
                elif isinstance(plan, str):
                    try:
                        import json

                        parsed = json.loads(plan)
                        if isinstance(parsed, dict):
                            error = str(parsed.get("error", ""))
                    except Exception:
                        error = ""
                filtered.append(
                    {"goal": goal_str, "goal_text": goal_str, "error": error}
                )
                if len(filtered) >= limit:
                    break
            return RecallResult(filtered)
        except Exception as exc:
            # Was: silently return this replica's in-process failures as truth.
            _log.warning("execution_memory_recall_failures_db_failed", error=str(exc)[:300])
            return RecallResult(degraded=True)

    async def recall_async(
        self,
        goal_hint: str,
        *,
        tenant_id: str,
        db: Any = None,
        limit: int = 3,
    ) -> RecallResult:
        """Recall relevant execution plans from DB for a given goal.

        Uses the in-memory ``_plans`` only when ``db`` is None (no DB
        configured). A failed query logs a warning and returns an empty
        ``RecallResult(degraded=True)`` — never this replica's local cache.
        """
        words = goal_hint.lower().split()[:5]
        if db is None:
            results: list[dict[str, Any]] = []
            for m in self._plans.get(tenant_id, []):
                goal_str = str(m.get("goal", m.get("goal_text", "")))
                if any(word in goal_str.lower() for word in words):
                    results.append(
                        {
                            "goal": goal_str,
                            "plan": m.get("plan", []) if isinstance(m.get("plan"), list) else [],
                            "success": m.get("success", True),
                        }
                    )
                    if len(results) >= limit:
                        break
            return RecallResult(results)

        try:
            from sqlalchemy import text

            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                        SELECT goal_text, plan, success FROM execution_memory
                        WHERE tenant_id = :tid AND success = TRUE
                        ORDER BY created_at DESC LIMIT :lim
                    """),
                        {"tid": tenant_id, "lim": limit * 3},
                    )
                ).fetchall()
        except Exception as exc:
            # Was: silently fall back to this replica's in-process plans.
            _log.warning("execution_memory_recall_db_failed", error=str(exc)[:300])
            return RecallResult(degraded=True)

        # Filter by keyword relevance
        filtered: list[dict[str, Any]] = []
        for row in rows:
            goal_text, plan, success = row
            if any(word in (goal_text or "").lower() for word in words):
                filtered.append(
                    {
                        "goal": goal_text,
                        "plan": plan if isinstance(plan, list) else [],
                        "success": success,
                    }
                )
                if len(filtered) >= limit:
                    break
        return RecallResult(filtered)
