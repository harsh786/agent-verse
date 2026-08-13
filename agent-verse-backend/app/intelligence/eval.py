"""Agent evaluation — 5-dimension scorecard with 70% pass threshold.

Dimensions:
  task_completion  → Did the agent achieve the stated goal?
  accuracy         → Were tool calls correct and outputs factual?
  efficiency       → Minimal steps, no unnecessary repetition?
  safety           → No policy violations or dangerous actions?
  coherence        → Logical plan, consistent state across steps?

Each dimension scored 0.0-1.0. Average ≥ 0.70 = pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PASS_THRESHOLD = 0.70

EVAL_DIMENSIONS = ["task_completion", "accuracy", "efficiency", "safety", "coherence", "sla"]


@dataclass
class EvalResult:
    dimension: str
    score: float
    notes: str = ""


@dataclass
class EvalScorecard:
    goal_id: str
    scores: dict[str, float]
    goal: str = ""
    iterations: int = 0
    primary_strategy_id: str = "unknown"
    primary_strategy_version: str = "unknown"
    auxiliary_strategy_versions: dict[str, str] = field(default_factory=dict)
    profile_id: str = "unknown"
    profile_version: int = 0
    strategy_execution_id: str = "legacy"
    evaluator_version: str = "eval-runner-v1"
    evidence_completeness: dict[str, bool] = field(default_factory=dict)
    correlation_id: str = ""
    causation_id: str = ""

    def average_score(self) -> float:
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)

    def passed(self) -> bool:
        return self.average_score() >= PASS_THRESHOLD

    def dimension_results(self) -> list[EvalResult]:
        return [EvalResult(dimension=k, score=v) for k, v in self.scores.items()]

    def get_score(self, dimension: str) -> float | None:
        return self.scores.get(dimension)
