"""AI Ops - observability, evals, regression, and drift models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DriftType(StrEnum):
    MODEL_OUTPUT = "model_output"
    RETRIEVAL = "retrieval"
    EMBEDDING = "embedding"
    PROMPT = "prompt"
    TOOL_RELIABILITY = "tool_reliability"
    GUARDRAIL_VIOLATIONS = "guardrail_violations"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class EvalDataset:
    """A dataset of golden input/output pairs for evaluation."""

    dataset_id: str
    tenant_id: str
    name: str
    description: str = ""
    golden_tasks: list[dict[str, Any]] = field(default_factory=list)
    created_at: str | None = None
    version: int = 1


@dataclass
class EvalResult:
    """Result of running an eval suite."""

    result_id: str
    dataset_id: str
    tenant_id: str
    goal_id: str | None = None
    scores: dict[str, float] = field(default_factory=dict)
    avg_score: float = 0.0
    passed: bool = False
    failed_tasks: list[dict[str, Any]] = field(default_factory=list)
    judge_model: str = ""
    created_at: str | None = None


@dataclass
class DriftAlert:
    """A drift detection alert."""

    alert_id: str
    tenant_id: str
    drift_type: DriftType
    severity: AlertSeverity
    metric_name: str
    baseline_value: float
    current_value: float
    drift_score: float
    message: str
    goal_id: str | None = None
    created_at: str | None = None


@dataclass
class LLMJudge:
    """An LLM-as-judge configuration."""

    judge_id: str
    tenant_id: str
    name: str
    provider: str
    model: str
    evaluation_dimensions: list[str] = field(default_factory=list)
    prompt_template: str = ""
    calibrated: bool = False
    created_at: str | None = None
