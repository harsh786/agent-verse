"""SelfImprovementEngine — translates scorecard results into improvement actions."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.evals.scoring_config import ImprovementThresholds

#: Feedback rows one tenant's run handles at most (the rest wait for the next run).
FEEDBACK_MAX_ROWS_PER_RUN = 5000

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.evals.runtime_scorecard import ScorecardResult
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class ImprovementAction(enum.StrEnum):
    UPDATE_PROMPT_VARIANT = "update_prompt_variant"
    UPDATE_MODEL_ROUTING = "update_model_routing"
    UPDATE_RAG_STRATEGY = "update_rag_strategy"
    STORE_REFLEXION_LESSON = "store_reflexion_lesson"
    BLACKLIST_TOOL_PATTERN = "blacklist_tool_pattern"
    CREATE_REGRESSION_CASE = "create_regression_case"


@dataclass
class ImprovementDecision:
    action_type: ImprovementAction
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SelfImprovementEngine:
    """Decides improvement actions from scorecard results."""

    def decide_actions(
        self,
        scorecard: ScorecardResult,
        profile: GoalRuntimeProfile,
        state: AgentState | None = None,
        thresholds: ImprovementThresholds | None = None,
    ) -> list[ImprovementDecision]:
        actions: list[ImprovementDecision] = []
        scores = scorecard.scores
        threshold = profile.eval_config.score_threshold
        thr = thresholds if thresholds is not None else ImprovementThresholds.from_settings()

        if scorecard.overall_score >= threshold:
            return []

        if (
            scores.get("rag_quality", 1.0) < thr.rag_quality_floor
            or scores.get("retrieval_confidence", 1.0) < thr.retrieval_confidence_floor
        ):
            actions.append(
                ImprovementDecision(
                    action_type=ImprovementAction.UPDATE_RAG_STRATEGY,
                    reason=(
                        f"rag_quality={scores.get('rag_quality', 0):.2f} "
                        f"below {thr.rag_quality_floor}"
                    ),
                    metadata={"current_rag_strategy": profile.rag_strategy.strategy},
                )
            )

        if (
            scores.get("goal_success", 1.0) < thr.goal_success_floor
            or scores.get("tool_success_rate", 1.0) < thr.tool_success_floor
        ):
            if state and (state.verification_feedback or "").strip():
                actions.append(
                    ImprovementDecision(
                        action_type=ImprovementAction.STORE_REFLEXION_LESSON,
                        reason="goal failed with actionable feedback",
                        metadata={"feedback": (state.verification_feedback or "")[:200]},
                    )
                )
            actions.append(
                ImprovementDecision(
                    action_type=ImprovementAction.UPDATE_PROMPT_VARIANT,
                    reason=(
                        f"goal_success={scores.get('goal_success', 0):.2f} "
                        f"below {thr.goal_success_floor}"
                    ),
                )
            )

        if scores.get("tool_success_rate", 1.0) < thr.tool_success_critical:
            actions.append(
                ImprovementDecision(
                    action_type=ImprovementAction.BLACKLIST_TOOL_PATTERN,
                    reason=f"tool_success_rate={scores.get('tool_success_rate', 0):.2f} critically low",  # noqa: E501
                )
            )

        if (
            scores.get("cost_efficiency", 1.0) < thr.cost_efficiency_floor
            or scores.get("latency", 1.0) < thr.latency_floor
        ):
            actions.append(
                ImprovementDecision(
                    action_type=ImprovementAction.UPDATE_MODEL_ROUTING,
                    reason="cost/latency score critically low",
                )
            )

        if scorecard.overall_score < thr.regression_case_floor:
            actions.append(
                ImprovementDecision(
                    action_type=ImprovementAction.CREATE_REGRESSION_CASE,
                    reason=(
                        f"overall_score={scorecard.overall_score:.2f} "
                        f"below {thr.regression_case_floor}"
                    ),
                )
            )

        return actions

    async def process_feedback_batch(
        self,
        *,
        db_session_factory: Any,
        tenant_id: str,
        batch_size: int = 100,
        max_rows: int = FEEDBACK_MAX_ROWS_PER_RUN,
        embedder: Any = None,
    ) -> dict[str, int]:
        """Read unprocessed rows from ``goal_feedback`` and derive improvement actions.

        Safe to call from a Celery beat task and concurrently from several
        workers: each batch is claimed with ``FOR UPDATE SKIP LOCKED`` and marked
        processed in the same transaction; batches repeat until the tenant's
        backlog is drained or ``max_rows`` were handled (it used to stop at one
        batch of 100 per daily run). A lesson's id is derived from its feedback
        row, so re-processing a row whose outer commit failed rewrites the same
        lesson instead of storing a duplicate. ``embedder`` (the platform's
        shared embedder) gives lessons a vector for semantic recall.

        Returns a summary dict: ``{"processed": N, "actions_derived": M}``.
        """
        processed = 0
        actions_derived = 0
        if db_session_factory is None:
            return {"processed": 0, "actions_derived": 0}
        skip: list[str] = []  # rows that failed this run: not retried until next run
        try:
            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            while processed + len(skip) < max_rows:
                limit = max(1, min(batch_size, max_rows - processed - len(skip)))
                async with (
                    db_session_factory() as session,
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    rows = (
                        await session.execute(
                            _t(
                                # goal_feedback's text column is ``correction``.
                                "SELECT id, goal_id, rating, correction AS feedback_text "
                                "FROM goal_feedback "
                                "WHERE tenant_id = :tid AND processed_at IS NULL "
                                "AND NOT (id = ANY(:skip)) "
                                "ORDER BY created_at ASC "
                                "LIMIT :lim FOR UPDATE SKIP LOCKED"
                            ),
                            {"tid": tenant_id, "lim": limit, "skip": skip},
                        )
                    ).fetchall()

                    for row in rows:
                        try:
                            rating = int(row.rating or 0)
                            feedback_text = row.feedback_text or ""
                            # Low rating → the correction becomes a failure-pattern
                            # lesson. The action counts only when the lesson was
                            # durably stored; a failed store leaves the row
                            # unprocessed for the next run.
                            if rating <= 2 and feedback_text.strip():
                                await _store_feedback_lesson(
                                    db_session_factory,
                                    tenant_id=tenant_id,
                                    feedback_id=str(row.id),
                                    goal_id=str(row.goal_id),
                                    lesson=feedback_text[:500],
                                    embedder=embedder,
                                )
                                actions_derived += 1
                            await session.execute(
                                _t("UPDATE goal_feedback SET processed_at = NOW() WHERE id = :id"),
                                {"id": row.id},
                            )
                            processed += 1
                        except Exception as row_exc:
                            import logging

                            skip.append(str(getattr(row, "id", "")))
                            logging.getLogger(__name__).warning(
                                "feedback_row_not_processed tenant=%s id=%s: %s",
                                tenant_id,
                                getattr(row, "id", "?"),
                                row_exc,
                            )
                            continue
                    await session.commit()
                if len(rows) < limit:
                    break  # backlog drained
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "feedback_batch_failed tenant=%s: %s", tenant_id, exc
            )
        return {"processed": processed, "actions_derived": actions_derived}


def feedback_lesson_id(tenant_id: str, feedback_id: str) -> str:
    """The long-term-memory id of the lesson derived from one feedback row."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"goal_feedback:{tenant_id}:{feedback_id}").hex


async def _store_feedback_lesson(
    db_session_factory: Any,
    *,
    tenant_id: str,
    feedback_id: str,
    goal_id: str,
    lesson: str,
    embedder: Any = None,
) -> str:
    """Persist a low-rating correction as a ``failure_pattern`` LTM lesson.

    Idempotent per feedback row (deterministic id; the store upserts on id).
    Raises (LongTermMemoryUnavailableError) when the lesson could not be stored.
    """
    from app.memory.long_term import LongTermMemory, LongTermMemoryStore
    from app.tenancy.context import PlanTier, TenantContext

    store = LongTermMemoryStore()
    store._db_factory = db_session_factory
    memory = LongTermMemory(
        content=f"[User feedback correction] {lesson}",
        source_goal_id=goal_id,
        memory_type="failure_pattern",
        confidence=0.9,
        tags=["goal_feedback", "self_improvement"],
        memory_id=feedback_lesson_id(tenant_id, feedback_id),
    )
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="self-improvement")
    return await store.store_async(
        memory=memory, tenant_ctx=ctx, db=db_session_factory, embedder=embedder
    )
