from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from typing import Any


class ExperimentType(str, enum.Enum):
    PLANNER_PROMPT = "planner_prompt"
    EXECUTOR_PROMPT = "executor_prompt"
    VERIFIER_PROMPT = "verifier_prompt"
    MODEL_ROUTING = "model_routing"
    RAG_STRATEGY = "rag_strategy"


_ARMS = ["control", "variant_a", "variant_b"]


@dataclass
class ExperimentArm:
    arm_id: str
    config: dict = field(default_factory=dict)


class ABTestingEngine:
    def __init__(self, db_factory: Any = None) -> None:
        self._results: dict[str, list[dict]] = {}
        self._db_factory = db_factory

    def get_experiment_arm(self, goal_id: str, experiment_type: ExperimentType) -> ExperimentArm:
        h = int(
            hashlib.md5(f"{goal_id}:{experiment_type.value}".encode()).hexdigest(),
            16,
        )
        arm_id = _ARMS[h % len(_ARMS)]
        return ExperimentArm(arm_id=arm_id, config={"arm": arm_id})

    def record_result(
        self,
        goal_id: str,
        experiment_type: ExperimentType,
        arm_id: str,
        score: float,
    ) -> None:
        key = experiment_type.value
        self._results.setdefault(key, []).append(
            {"goal_id": goal_id, "arm_id": arm_id, "score": score}
        )

    def get_arm_stats(self, experiment_type: ExperimentType, arm_id: str) -> dict:
        key = experiment_type.value
        results = [r for r in self._results.get(key, []) if r["arm_id"] == arm_id]
        if not results:
            return {"call_count": 0, "avg_score": 0.0}
        avg = sum(r["score"] for r in results) / len(results)
        return {"call_count": len(results), "avg_score": round(avg, 3)}

    def can_promote_variant(
        self,
        experiment_type: ExperimentType,
        arm_id: str,
        min_score_threshold: float,
        current_score: float,
    ) -> bool:
        stats = self.get_arm_stats(experiment_type, arm_id)
        if stats["call_count"] < 5:
            return current_score >= min_score_threshold
        return stats["avg_score"] >= min_score_threshold

    async def record_result_async(
        self,
        goal_id: str,
        experiment_type: ExperimentType,
        arm_id: str,
        score: float,
        tenant_id: str = "unknown",
    ) -> None:
        """Record result in-memory AND persist to ab_test_results table."""
        # Always write in-memory first
        self.record_result(goal_id, experiment_type, arm_id, score)
        # Best-effort DB write
        if self._db_factory is None:
            return
        try:
            import uuid

            from sqlalchemy import text

            async with self._db_factory() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO ab_test_results
                            (id, goal_id, tenant_id, experiment_type, arm_id, score, created_at)
                        VALUES
                            (:id, :goal_id, :tenant_id, :experiment_type, :arm_id, :score, NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "goal_id": goal_id,
                        "tenant_id": tenant_id,
                        "experiment_type": experiment_type.value,
                        "arm_id": arm_id,
                        "score": score,
                    },
                )
        except Exception as exc:
            try:
                import logging

                logging.getLogger(__name__).warning("ab_test_result_persist_failed: %s", exc)
            except Exception:
                pass

    async def load_from_db(
        self,
        *,
        db_factory: Any | None = None,
        limit: int = 1000,
    ) -> int:
        """Seed in-memory results from DB on startup."""
        factory = db_factory or self._db_factory
        if factory is None:
            return 0
        try:
            from sqlalchemy import text

            count = 0
            async with factory() as session:
                rows = (
                    await session.execute(
                        text("""
                    SELECT goal_id, experiment_type, arm_id, score
                    FROM ab_test_results
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                        {"limit": limit},
                    )
                ).fetchall()
                for row in rows:
                    goal_id, exp_type_str, arm_id, score = row[0], row[1], row[2], row[3]
                    try:
                        exp_type = ExperimentType(exp_type_str)
                        self.record_result(goal_id, exp_type, arm_id, float(score))
                        count += 1
                    except Exception:
                        pass
            return count
        except Exception:
            return 0


ab_testing_engine = ABTestingEngine()
