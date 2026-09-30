"""Verifier calibration — tracks verdicts vs actual outcomes to measure false-confirm rate.

Enables the Phase 3 Track E goal: false-confirm rate ≤ 2%.

A false-positive (false-confirm) occurs when:
  verifier_verdict = True  AND  actual_outcome = False
"""

from __future__ import annotations

import collections
import uuid
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Verdicts kept in the per-process buffer (the table is the record).
DEFAULT_MAX_RECORDS = 10_000


class VerifierCalibrationStore:
    """Records verifier verdicts and eventual outcomes for calibration.

    Enables false-confirm rate measurement::

        false_confirm_rate = false_positives / total_resolved_records

    where a *false_positive* is a record where the verifier said SUCCESS
    but the actual outcome was FAILURE.

    With a ``db_factory`` every verdict is persisted to ``verifier_calibration``
    (tenant RLS) and outcomes are written there by goal, so feedback reaches a
    verdict recorded by any replica or worker. The in-memory buffer is a
    bounded ring of this process's recent verdicts for quick stats — it used to
    grow on every verdict and be scanned linearly per feedback request.
    """

    def __init__(self, db_factory: Any = None, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._db = db_factory
        self._records: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=max(1, max_records)
        )

    async def record_verdict(
        self,
        *,
        goal_id: str,
        tenant_id: str,
        verifier_verdict: bool,
        verifier_model: str = "",
        iteration: int = 1,
        goal_text: str = "",
        verifier_source: str = "primary",
    ) -> str:
        """Record a verifier verdict. Returns the new ``record_id``."""
        record_id = uuid.uuid4().hex
        record: dict[str, Any] = {
            "id": record_id,
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "verifier_verdict": verifier_verdict,
            "actual_outcome": None,  # filled in by record_actual_outcome*()
            "verifier_model": verifier_model,
            "verifier_source": verifier_source,
            "iteration": iteration,
            "goal_text": goal_text[:200],
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._records.append(record)

        if self._db is not None:
            try:
                from sqlalchemy import text

                # The table's real columns (migration 0073). This used to write
                # predicted_success / verifier_source, which do not exist, so no
                # verdict was ever persisted (and the error was logged at debug).
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    await session.execute(
                        text("""
                            INSERT INTO verifier_calibration
                            (id, tenant_id, goal_id, verifier_verdict,
                             verifier_model, iteration, goal_text, created_at)
                            VALUES (:id, :tid, :gid, :verdict, :model, :iter, :goal, NOW())
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": record_id,
                            "tid": tenant_id,
                            "gid": goal_id,
                            "verdict": verifier_verdict,
                            "model": verifier_model[:128],
                            "iter": iteration,
                            "goal": goal_text[:200],
                        },
                    )
            except Exception as exc:
                logger.warning("calibration_record_failed", goal_id=goal_id, error=str(exc)[:200])

        return record_id

    async def record_actual_outcome(
        self,
        record_id: str,
        actual_success: bool,
        tenant_id: str | None = None,
    ) -> None:
        """Update one record (by id) with the actual outcome.

        ``tenant_id`` may be omitted by older callers; it is then recovered from
        the in-process record. If neither is available the row cannot be scoped,
        so the DB write is skipped rather than issued unscoped.
        """
        for r in self._records:
            if r["id"] == record_id:
                r["actual_outcome"] = actual_success
                tenant_id = tenant_id or r.get("tenant_id")
                break

        if self._db is not None and tenant_id:
            try:
                from sqlalchemy import text

                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    await session.execute(
                        text("""
                            UPDATE verifier_calibration
                            SET actual_outcome = :outcome
                            WHERE id = :id AND tenant_id = :tid
                        """),
                        {"outcome": actual_success, "id": record_id, "tid": tenant_id},
                    )
            except Exception as exc:
                logger.warning("calibration_update_failed", error=str(exc)[:200])

    async def record_actual_outcome_by_goal(
        self, *, goal_id: str, tenant_id: str, actual_success: bool
    ) -> int:
        """Attach a human/eval outcome to the goal's final verdict. Returns rows updated.

        Written in Postgres by goal under the tenant's RLS context, so it works
        for a goal verified by another replica or a Celery worker. The goal's
        LATEST verdict (highest iteration) is the one the outcome judges —
        earlier iterations judged intermediate states. A DB error propagates
        (the caller decides how loudly to report it).
        """
        latest: dict[str, Any] | None = None
        for r in self._records:
            if r["goal_id"] == goal_id and r["tenant_id"] == tenant_id and (
                latest is None or int(r.get("iteration") or 0) >= int(latest.get("iteration") or 0)
            ):
                latest = r
        if latest is not None:
            latest["actual_outcome"] = actual_success

        if self._db is None:
            return 1 if latest is not None else 0

        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            result = await session.execute(
                text("""
                    UPDATE verifier_calibration SET actual_outcome = :outcome
                    WHERE id = (
                        SELECT id FROM verifier_calibration
                        WHERE tenant_id = :tid AND goal_id = :gid
                        ORDER BY iteration DESC, created_at DESC
                        LIMIT 1
                    ) AND tenant_id = :tid
                """),
                {"outcome": actual_success, "tid": tenant_id, "gid": goal_id},
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def afalse_confirm_rate(self, tenant_id: str) -> dict[str, Any]:
        """The tenant's false-confirm rate from ``verifier_calibration`` (every replica's
        verdicts), under RLS. Without a DB, this process's buffer. A DB error raises.
        """
        if self._db is None:
            return self.false_confirm_rate(tenant_id)
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text("""
                        SELECT
                            COUNT(*) FILTER (WHERE actual_outcome IS NOT NULL),
                            COUNT(*) FILTER (WHERE verifier_verdict AND actual_outcome = FALSE),
                            COUNT(*) FILTER (WHERE NOT verifier_verdict AND actual_outcome)
                        FROM verifier_calibration WHERE tenant_id = :tid
                    """),
                    {"tid": tenant_id},
                )
            ).fetchone()
        total, fps, fns = (int(v or 0) for v in (row or (0, 0, 0)))
        rate = fps / total if total else 0.0
        return {
            "rate": rate,
            "total": total,
            "false_positives": fps,
            "false_negatives": fns,
            "target_rate": 0.02,
            "on_target": rate <= 0.02,
        }

    def false_confirm_rate(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Compute false-confirm rate across this process's resolved records.

        A *false_positive* means the verifier said SUCCESS but the actual outcome
        was FAILURE.

        Returns a dict with keys:
          - ``rate``: float in [0, 1]
          - ``total``: number of resolved records
          - ``false_positives``: count of false positives
          - ``false_negatives``: count of false negatives
          - ``target_rate``: 0.02  (2% Phase 3 target)
          - ``on_target``: bool
        """
        records = [
            r
            for r in self._records
            if r.get("actual_outcome") is not None
            and (tenant_id is None or r["tenant_id"] == tenant_id)
        ]

        if not records:
            return {
                "rate": 0.0,
                "total": 0,
                "false_positives": 0,
                "false_negatives": 0,
                "target_rate": 0.02,
                "on_target": True,
            }

        false_positives = sum(
            1 for r in records if r["verifier_verdict"] is True and r["actual_outcome"] is False
        )
        false_negatives = sum(
            1 for r in records if r["verifier_verdict"] is False and r["actual_outcome"] is True
        )
        rate = false_positives / len(records)

        return {
            "rate": rate,
            "total": len(records),
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "target_rate": 0.02,
            "on_target": rate <= 0.02,
        }


# ---------------------------------------------------------------------------
# Module-level singleton — used as the default when no store is injected
# ---------------------------------------------------------------------------

_default_calibration_store = VerifierCalibrationStore()
