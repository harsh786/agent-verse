"""Verifier calibration — tracks verdicts vs actual outcomes to measure false-confirm rate.

Enables the Phase 3 Track E goal: false-confirm rate ≤ 2%.

A false-positive (false-confirm) occurs when:
  verifier_verdict = True  AND  actual_outcome = False
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


class VerifierCalibrationStore:
    """Records verifier verdicts and eventual outcomes for calibration.

    Enables false-confirm rate measurement::

        false_confirm_rate = false_positives / total_resolved_records

    where a *false_positive* is a record where the verifier said SUCCESS
    but the actual outcome was FAILURE.

    The store maintains an in-memory buffer for fast stats without requiring
    a running DB.  When a ``db_factory`` is provided the records are also
    persisted asynchronously.
    """

    def __init__(self, db_factory: Any = None) -> None:
        self._db = db_factory
        self._records: list[dict[str, Any]] = []

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
            "actual_outcome": None,  # filled in by record_actual_outcome()
            "verifier_model": verifier_model,
            "verifier_source": verifier_source,
            "iteration": iteration,
            "goal_text": goal_text[:200],
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._records.append(record)

        # Async DB persistence (best-effort)
        if self._db is not None:
            try:
                from sqlalchemy import text
                async with self._db() as session, session.begin():
                    await session.execute(
                        text("""
                            INSERT INTO verifier_calibration
                            (id, tenant_id, goal_id, verifier_source, predicted_success,
                             verifier_model, iteration, goal_text, created_at)
                            VALUES (:id, :tid, :gid, :src, :verdict, :model, :iter, :goal, NOW())
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": record_id,
                            "tid": tenant_id,
                            "gid": goal_id,
                            "src": verifier_source,
                            "verdict": verifier_verdict,
                            "model": verifier_model,
                            "iter": iteration,
                            "goal": goal_text[:200],
                        },
                    )
            except Exception as exc:
                logger.debug("calibration_record_failed", error=str(exc)[:60])

        return record_id

    async def record_actual_outcome(
        self,
        record_id: str,
        actual_success: bool,
    ) -> None:
        """Update a record with the actual outcome (from human eval or next replan)."""
        for r in self._records:
            if r["id"] == record_id:
                r["actual_outcome"] = actual_success
                break

        if self._db is not None:
            try:
                from sqlalchemy import text
                async with self._db() as session, session.begin():
                    await session.execute(
                        text("""
                            UPDATE verifier_calibration
                            SET actual_success = :outcome
                            WHERE id = :id
                        """),
                        {"outcome": actual_success, "id": record_id},
                    )
            except Exception as exc:
                logger.debug("calibration_update_failed", error=str(exc)[:60])

    def false_confirm_rate(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Compute false-confirm rate across all resolved records.

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
            r for r in self._records
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
            1 for r in records
            if r["verifier_verdict"] is True and r["actual_outcome"] is False
        )
        false_negatives = sum(
            1 for r in records
            if r["verifier_verdict"] is False and r["actual_outcome"] is True
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
