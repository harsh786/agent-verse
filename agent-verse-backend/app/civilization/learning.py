"""LearningPipeline — curated collective learning (anti-poisoning gated by EvalRunner).

State machine: candidate → validated|rejected → promoted
Only validated candidates are promoted to LongTermMemoryStore.
Rejected candidates NEVER reach shared memory.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.civilization.events import emit_event, CivEventType
from app.observability.logging import get_logger

logger = get_logger(__name__)

_PROMOTION_SCORE_THRESHOLD = 0.7   # Minimum eval score to promote
_REJECTION_SCORE_THRESHOLD = 0.35  # Below this → rejected


class LearningPipeline:
    """Curated collective learning pipeline.

    Agents submit candidates. EvalRunner validates. Good ones are promoted
    to LongTermMemoryStore (shared across the civilization).
    Rejected ones NEVER reach memory — prevents bad-learning contamination.
    """

    def __init__(
        self,
        *,
        civilization_id: str,
        tenant_id: str,
        db_session_factory: Any = None,
        eval_runner: Any = None,
        long_term_memory: Any = None,
        bus: Any = None,
        redis: Any = None,
    ) -> None:
        self._civ_id = civilization_id
        self._tenant_id = tenant_id
        self._db = db_session_factory
        self._eval_runner = eval_runner
        self._ltm = long_term_memory
        self._bus = bus
        self._redis = redis

    async def submit_candidate(
        self,
        *,
        agent_id: str,
        candidate_text: str,
        tenant_ctx: Any,
    ) -> str:
        """An agent submits a learning candidate. Returns candidate_id."""
        candidate_id = uuid.uuid4().hex
        if self._db is not None:
            try:
                from sqlalchemy import text
                async with self._db() as session, session.begin():
                    await session.execute(text("""
                        INSERT INTO civilization_learnings
                            (id, civilization_id, tenant_id, candidate, source_agent_id, status, created_at)
                        VALUES (:id, :cid, :tid, :candidate, :agent, 'candidate', NOW())
                    """), {
                        "id": candidate_id, "cid": self._civ_id, "tid": self._tenant_id,
                        "candidate": candidate_text[:2000], "agent": agent_id,
                    })
            except Exception as exc:
                logger.warning("learning_submit_candidate_failed", error=str(exc))

        await emit_event(
            civilization_id=self._civ_id, tenant_id=self._tenant_id,
            event_type=CivEventType.LEARNING_CANDIDATE,
            payload={"candidate_id": candidate_id, "agent_id": agent_id,
                     "preview": candidate_text[:100]},
            db=self._db, redis=self._redis,
        )

        if self._bus is not None:
            await self._bus.publish(
                from_agent_id=agent_id,
                topic="coordination",
                payload={"event": "learning_candidate_submitted",
                         "candidate_id": candidate_id, "preview": candidate_text[:100]},
            )

        return candidate_id

    async def run_step(self, batch_size: int = 10) -> dict:
        """Process a batch of pending candidates.

        This is the Celery-callable step.
        Returns: {"validated": N, "promoted": N, "rejected": N}
        """
        candidates = await self._get_pending_candidates(batch_size)
        if not candidates:
            return {"validated": 0, "promoted": 0, "rejected": 0}

        validated = 0
        promoted = 0
        rejected = 0

        for candidate in candidates:
            try:
                result = await self._process_candidate(candidate)
                if result == "promoted":
                    promoted += 1
                    validated += 1
                elif result == "rejected":
                    rejected += 1
                else:
                    validated += 1  # validated but below promotion threshold
            except Exception as exc:
                logger.warning("learning_process_candidate_failed",
                               candidate_id=candidate.get("id"), error=str(exc))

        logger.info(
            "learning_pipeline_step",
            civilization_id=self._civ_id,
            validated=validated, promoted=promoted, rejected=rejected,
        )
        return {"validated": validated, "promoted": promoted, "rejected": rejected}

    async def _process_candidate(self, candidate: dict) -> str:
        """Score and decide on a single candidate. Returns 'promoted'|'rejected'|'validated'."""
        candidate_id = candidate["id"]
        candidate_text = candidate["candidate"]
        source_agent_id = candidate["source_agent_id"]

        # Evaluate via EvalRunner (if available)
        eval_score = None
        if self._eval_runner is not None:
            try:
                # Use a lightweight eval: score the candidate as a response to itself
                # In production, this would be scored against the originating task outcome
                state = _FakeScoringState(goal=candidate_text, steps=[])
                from app.tenancy.context import TenantContext, PlanTier
                fake_ctx = TenantContext(
                    tenant_id=self._tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="learning"
                )
                scorecard = await self._eval_runner.score_and_persist(
                    state, fake_ctx, db=self._db
                )
                if hasattr(scorecard, "average_score") and callable(scorecard.average_score):
                    eval_score = scorecard.average_score()
                elif hasattr(scorecard, "average_score"):
                    eval_score = float(scorecard.average_score or 0.5)
                else:
                    eval_score = 0.5
            except Exception as exc:
                logger.warning("learning_eval_failed", candidate_id=candidate_id, error=str(exc))
                eval_score = 0.5  # Default on eval failure

        eval_score = eval_score if eval_score is not None else 0.5

        # Decide
        if eval_score < _REJECTION_SCORE_THRESHOLD:
            await self._set_candidate_status(candidate_id, "rejected", eval_score)
            await emit_event(
                civilization_id=self._civ_id, tenant_id=self._tenant_id,
                event_type=CivEventType.LEARNING_REJECTED,
                payload={"candidate_id": candidate_id, "eval_score": eval_score,
                         "reason": "below rejection threshold"},
                db=self._db, redis=self._redis,
            )
            return "rejected"

        # Validate
        await self._set_candidate_status(candidate_id, "validated", eval_score)

        # Promote if above threshold
        if eval_score >= _PROMOTION_SCORE_THRESHOLD:
            memory_id = await self._promote_to_ltm(
                candidate_id=candidate_id,
                candidate_text=candidate_text,
                source_agent_id=source_agent_id,
            )
            if memory_id:
                await self._set_candidate_promoted(candidate_id, memory_id, eval_score)
                await emit_event(
                    civilization_id=self._civ_id, tenant_id=self._tenant_id,
                    event_type=CivEventType.LEARNING_PROMOTED,
                    payload={"candidate_id": candidate_id, "memory_id": memory_id,
                             "eval_score": eval_score},
                    db=self._db, redis=self._redis,
                )
                return "promoted"

        return "validated"

    async def _promote_to_ltm(
        self,
        *,
        candidate_id: str,
        candidate_text: str,
        source_agent_id: str,
    ) -> str | None:
        """Write validated candidate to LongTermMemoryStore."""
        if self._ltm is None:
            return None
        try:
            from app.memory.long_term import LongTermMemory
            from app.tenancy.context import TenantContext, PlanTier
            fake_ctx = TenantContext(
                tenant_id=self._tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="learning"
            )
            memory = LongTermMemory(
                content=f"[Civilization Learning] {candidate_text}",
                source_goal_id=source_agent_id,
                memory_type="civilization_learning",
                confidence=0.85,
                tags=["civilization", self._civ_id, "collective_learning"],
            )
            await self._ltm.store_async(
                memory=memory, tenant_ctx=fake_ctx, db=self._db
            )
            memory_id = memory.memory_id if hasattr(memory, "memory_id") else uuid.uuid4().hex
            logger.info("learning_promoted_to_ltm", candidate_id=candidate_id,
                        civilization_id=self._civ_id)
            return memory_id
        except Exception as exc:
            logger.warning("learning_promote_to_ltm_failed", candidate_id=candidate_id, error=str(exc))
            return None

    async def get_learnings(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get learning records for the UI Learning Ledger."""
        if self._db is None:
            return []
        try:
            from sqlalchemy import text
            conditions = ["civilization_id = :cid", "tenant_id = :tid"]
            params: dict = {"cid": self._civ_id, "tid": self._tenant_id, "limit": limit}
            if status:
                conditions.append("status = :status")
                params["status"] = status
            where = " AND ".join(conditions)
            async with self._db() as session:
                rows = (await session.execute(text(
                    f"SELECT id, candidate, source_agent_id, status, eval_score, "  # noqa: S608
                    f"promoted_memory_id, created_at, decided_at "
                    f"FROM civilization_learnings WHERE {where} "
                    f"ORDER BY created_at DESC LIMIT :limit"
                ), params)).fetchall()
            return [
                {
                    "id": r[0], "candidate": r[1][:200], "source_agent_id": r[2],
                    "status": r[3], "eval_score": r[4],
                    "promoted_memory_id": r[5],
                    "created_at": r[6].isoformat() if r[6] else "",
                    "decided_at": r[7].isoformat() if r[7] else "",
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("learning_get_learnings_failed", error=str(exc))
            return []

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_pending_candidates(self, limit: int) -> list[dict]:
        if self._db is None:
            return []
        try:
            from sqlalchemy import text
            async with self._db() as session:
                rows = (await session.execute(text("""
                    SELECT id, candidate, source_agent_id
                    FROM civilization_learnings
                    WHERE civilization_id = :cid AND tenant_id = :tid AND status = 'candidate'
                    ORDER BY created_at ASC LIMIT :limit
                """), {"cid": self._civ_id, "tid": self._tenant_id, "limit": limit})).fetchall()
            return [{"id": r[0], "candidate": r[1], "source_agent_id": r[2]} for r in rows]
        except Exception as exc:
            logger.warning("learning_get_pending_failed", error=str(exc))
            return []

    async def _set_candidate_status(self, candidate_id: str, status: str, eval_score: float) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text("""
                    UPDATE civilization_learnings
                    SET status = :status, eval_score = :score, decided_at = NOW()
                    WHERE id = :id AND tenant_id = :tid
                """), {"status": status, "score": eval_score,
                       "id": candidate_id, "tid": self._tenant_id})
        except Exception as exc:
            logger.warning("learning_set_status_failed", error=str(exc))

    async def _set_candidate_promoted(
        self, candidate_id: str, memory_id: str, eval_score: float
    ) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import text
            async with self._db() as session, session.begin():
                await session.execute(text("""
                    UPDATE civilization_learnings
                    SET status = 'promoted', promoted_memory_id = :mem_id,
                        eval_score = :score, decided_at = NOW()
                    WHERE id = :id AND tenant_id = :tid
                """), {"mem_id": memory_id, "score": eval_score,
                       "id": candidate_id, "tid": self._tenant_id})
        except Exception as exc:
            logger.warning("learning_set_promoted_failed", error=str(exc))


class _FakeScoringState:
    """Minimal state stub for EvalRunner.score_and_persist when no real state available."""
    def __init__(self, goal: str, steps: list) -> None:
        self.goal = goal
        self.goal_id = uuid.uuid4().hex
        self.steps = steps
        self.status = "complete"
        self.error_message = ""
        self.verification_success = True
        self.context: dict = {}
