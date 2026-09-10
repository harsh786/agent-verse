"""SelfImprovementEngine — translates scorecard results into improvement actions."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.evals.scoring_config import ImprovementThresholds

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
    ) -> dict[str, int]:
        """Read unprocessed rows from ``goal_feedback`` and derive improvement actions.

        This method is safe to call from a Celery beat task — it is idempotent and
        marks each processed row so it is not re-processed on the next run.

        Returns a summary dict: ``{"processed": N, "actions_derived": M}``.
        """
        processed = 0
        actions_derived = 0
        if db_session_factory is None:
            return {"processed": 0, "actions_derived": 0}
        try:
            from sqlalchemy import text as _t

            from app.db.rls import sqlalchemy_rls_context

            async with db_session_factory() as session, sqlalchemy_rls_context(session, tenant_id):
                rows = (
                    await session.execute(
                        _t(
                            "SELECT id, goal_id, rating, feedback_text, metadata "
                            "FROM goal_feedback "
                            "WHERE tenant_id = :tid AND processed_at IS NULL "
                            "ORDER BY created_at ASC "
                            "LIMIT :lim"
                        ),
                        {"tid": tenant_id, "lim": batch_size},
                    )
                ).fetchall()

                for row in rows:
                    try:
                        rating = int(row.rating or 0)
                        feedback_text = row.feedback_text or ""
                        # Low rating → store reflexion lesson
                        if rating <= 2 and feedback_text.strip():
                            actions_derived += 1
                            # Persist lesson into long-term memory if available
                            try:
                                from app.memory.long_term_memory import LongTermMemoryStore

                                ltm = LongTermMemoryStore(db_session_factory)
                                await ltm.store_lesson(
                                    tenant_id=tenant_id,
                                    goal_id=str(row.goal_id),
                                    lesson=feedback_text[:500],
                                )
                            except Exception:
                                pass
                        await session.execute(
                            _t("UPDATE goal_feedback SET processed_at = NOW() WHERE id = :id"),
                            {"id": row.id},
                        )
                        processed += 1
                    except Exception:
                        continue
                await session.commit()
        except Exception:
            pass
        return {"processed": processed, "actions_derived": actions_derived}
