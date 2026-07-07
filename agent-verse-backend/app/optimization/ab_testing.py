from __future__ import annotations
import enum
import hashlib
from dataclasses import dataclass, field


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
    def __init__(self) -> None:
        self._results: dict[str, list[dict]] = {}

    def get_experiment_arm(
        self, goal_id: str, experiment_type: ExperimentType
    ) -> ExperimentArm:
        h = int(
            hashlib.md5(
                f"{goal_id}:{experiment_type.value}".encode()
            ).hexdigest(),
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

    def get_arm_stats(
        self, experiment_type: ExperimentType, arm_id: str
    ) -> dict:
        key = experiment_type.value
        results = [
            r for r in self._results.get(key, []) if r["arm_id"] == arm_id
        ]
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


ab_testing_engine = ABTestingEngine()
