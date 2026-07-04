"""
Experiment Registry
===================
Single control plane for ALL self-improvement experiments:
- PromptOptimizer A/B tests
- SelfOptimizerV2 config proposals
- Skill instruction variants
- Model routing experiments

Enforces: one active experiment per (tenant_id, agent_id) at a time.
All experiments go through: proposed → offline-eval → live A/B → promoted/rolled_back.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Valid experiment statuses
_VALID_STATUSES = frozenset({
    "proposed",
    "offline_eval",
    "live_ab",
    "promoted",
    "rolled_back",
    "inconclusive",
})

# Minimum sample size before making promotion decisions
MIN_SAMPLES_PER_ARM = 20

# Statistically significant improvement threshold
MIN_IMPROVEMENT_PCT = 5.0  # 5% improvement required


class ExperimentRegistry:
    """In-memory experiment registry (upgraded with DB in lifespan).

    Enforces one active experiment per (tenant_id, agent_id).
    """

    def __init__(self) -> None:
        self._experiments: dict[str, dict[str, Any]] = {}  # exp_id → experiment dict
        self._active_per_agent: dict[tuple[str, str], str] = {}  # (tenant, agent) → exp_id

    def propose(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        name: str,
        experiment_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Register a new experiment. Returns the experiment dict."""
        agent_key = agent_id or "__global__"
        lock_key = (tenant_id, agent_key)

        # Check for existing active experiment
        existing_id = self._active_per_agent.get(lock_key)
        if existing_id and existing_id in self._experiments:
            existing = self._experiments[existing_id]
            if existing.get("status") in ("proposed", "offline_eval", "live_ab"):
                raise ValueError(
                    f"Experiment '{existing['name']}' is already active for "
                    f"agent '{agent_key}'. Complete or roll back it first."
                )

        exp_id = uuid.uuid4().hex[:16]
        experiment: dict[str, Any] = {
            "id": exp_id,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "name": name,
            "status": "proposed",
            "experiment_type": experiment_type,
            "config": config,
            "n_control": 0,
            "n_treatment": 0,
            "control_metrics": None,
            "treatment_metrics": None,
            "verdict": None,
        }

        self._experiments[exp_id] = experiment
        self._active_per_agent[lock_key] = exp_id

        logger.info("experiment_proposed", exp_id=exp_id, name=name, type=experiment_type)
        return experiment

    def record_outcome(
        self,
        exp_id: str,
        *,
        arm: str,  # "control" | "treatment"
        score: float,
    ) -> None:
        """Record one outcome for an experiment arm."""
        exp = self._experiments.get(exp_id)
        if exp is None:
            return

        if arm == "control":
            exp["n_control"] += 1
            metrics: dict[str, Any] = exp.get("control_metrics") or {
                "scores": [],
                "mean": 0.0,
                "variance": 0.0,
            }
            metrics["scores"] = [*metrics.get("scores", []), score]
            self._update_stats(metrics)
            exp["control_metrics"] = metrics
        elif arm == "treatment":
            exp["n_treatment"] += 1
            metrics = exp.get("treatment_metrics") or {
                "scores": [],
                "mean": 0.0,
                "variance": 0.0,
            }
            metrics["scores"] = [*metrics.get("scores", []), score]
            self._update_stats(metrics)
            exp["treatment_metrics"] = metrics

    def evaluate(self, exp_id: str) -> dict[str, Any]:
        """Evaluate whether the experiment can make a promotion decision."""
        exp = self._experiments.get(exp_id)
        if exp is None:
            return {"decision": "unknown", "reason": "Experiment not found"}

        n_c = exp.get("n_control", 0)
        n_t = exp.get("n_treatment", 0)

        if n_c < MIN_SAMPLES_PER_ARM or n_t < MIN_SAMPLES_PER_ARM:
            return {
                "decision": "insufficient_data",
                "reason": (
                    f"Need {MIN_SAMPLES_PER_ARM} samples per arm. "
                    f"Have control={n_c}, treatment={n_t}"
                ),
            }

        ctrl_metrics = exp.get("control_metrics") or {}
        treat_metrics = exp.get("treatment_metrics") or {}
        ctrl_mean: float = ctrl_metrics.get("mean", 0)
        treat_mean: float = treat_metrics.get("mean", 0)

        if ctrl_mean == 0:
            return {"decision": "insufficient_data", "reason": "Control has zero mean"}

        improvement_pct = ((treat_mean - ctrl_mean) / ctrl_mean) * 100

        if improvement_pct >= MIN_IMPROVEMENT_PCT:
            return {
                "decision": "promote",
                "improvement_pct": round(improvement_pct, 2),
                "ctrl_mean": ctrl_mean,
                "treat_mean": treat_mean,
            }
        elif improvement_pct < -MIN_IMPROVEMENT_PCT:
            return {
                "decision": "rollback",
                "improvement_pct": round(improvement_pct, 2),
            }
        else:
            return {
                "decision": "inconclusive",
                "improvement_pct": round(improvement_pct, 2),
            }

    def promote(self, exp_id: str) -> bool:
        exp = self._experiments.get(exp_id)
        if not exp:
            return False
        exp["status"] = "promoted"
        exp["verdict"] = "promoted"
        self._clear_active(exp)
        logger.info("experiment_promoted", exp_id=exp_id, name=exp.get("name"))
        return True

    def rollback(self, exp_id: str) -> bool:
        exp = self._experiments.get(exp_id)
        if not exp:
            return False
        exp["status"] = "rolled_back"
        exp["verdict"] = "rolled_back"
        self._clear_active(exp)
        logger.info("experiment_rolled_back", exp_id=exp_id)
        return True

    def list_experiments(self, tenant_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return [
            e
            for e in self._experiments.values()
            if e["tenant_id"] == tenant_id
            and (status is None or e["status"] == status)
        ]

    @staticmethod
    def _update_stats(metrics: dict[str, Any]) -> None:
        """Update running mean and variance using Welford's algorithm."""
        scores: list[float] = metrics.get("scores", [])
        if not scores:
            return
        n = len(scores)
        mean = sum(scores) / n
        variance = sum((s - mean) ** 2 for s in scores) / max(n - 1, 1)
        metrics["mean"] = mean
        metrics["variance"] = variance
        metrics["n"] = n

    def _clear_active(self, exp: dict[str, Any]) -> None:
        agent_key = exp.get("agent_id") or "__global__"
        lock_key = (exp["tenant_id"], agent_key)
        if self._active_per_agent.get(lock_key) == exp["id"]:
            del self._active_per_agent[lock_key]


# Module-level singleton
_default_registry = ExperimentRegistry()

__all__ = [
    "MIN_IMPROVEMENT_PCT",
    "MIN_SAMPLES_PER_ARM",
    "ExperimentRegistry",
    "_default_registry",
]
