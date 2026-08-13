"""Quantitative, fail-closed promotion policy for agent-pattern canaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CertificationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "agent-pattern-rollout-v1"
    minimum_samples: int = 300
    internal_observation_hours: int = 24
    cohort_observation_hours: int = 72
    evidence_max_age_hours: int = 96
    max_quality_regression: float = 0.05
    max_cost_regression: float = 0.10
    max_p95_latency_regression: float = 0.10
    max_p99_latency_regression: float = 0.15
    max_policy_denial_rate: float = 0.05
    max_outbox_lag_seconds: float = 10.0
    max_lease_reclaim_rate: float = 0.05
    min_rollback_success_rate: float = 1.0
    approval_authority: str = "agent-pattern-release-owner"


class CanaryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern_id: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    sample_size: int = Field(ge=0)
    quality_regression: float
    cost_regression: float
    p95_latency_regression: float
    p99_latency_regression: float
    policy_denial_rate: float = Field(ge=0)
    isolation_events: int = Field(ge=0)
    sandbox_escape_events: int = Field(ge=0)
    approval_bypass_events: int = Field(ge=0)
    outbox_lag_seconds: float = Field(ge=0)
    lease_reclaim_rate: float = Field(ge=0)
    rollback_success_rate: float = Field(ge=0, le=1)
    observed_hours: int = Field(ge=0)
    captured_at: datetime
    signed: bool
    baseline_digest: str = Field(min_length=1)
    evidence_digest: str = Field(min_length=1)

    @field_validator("captured_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("captured_at must be UTC-aware")
        return value


class RolloutDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: Literal["promote", "hold", "rollback"]
    policy_version: str
    reasons: tuple[str, ...]


def decide_rollout(
    policy: CertificationPolicy, evidence: CanaryEvidence | None
) -> RolloutDecision:
    """Return a deterministic promotion decision; absent trust evidence always holds."""
    if evidence is None:
        return RolloutDecision(
            decision="hold", policy_version=policy.version, reasons=("missing_evidence",)
        )
    trust_reasons: list[str] = []
    if not evidence.signed:
        trust_reasons.append("unsigned_evidence")
    if datetime.now(UTC) - evidence.captured_at > timedelta(
        hours=policy.evidence_max_age_hours
    ):
        trust_reasons.append("stale_evidence")
    if not evidence.baseline_digest.startswith("sha256:"):
        trust_reasons.append("invalid_baseline_digest")
    if not evidence.evidence_digest.startswith("sha256:"):
        trust_reasons.append("invalid_evidence_digest")
    if trust_reasons:
        return RolloutDecision(
            decision="hold", policy_version=policy.version, reasons=tuple(trust_reasons)
        )

    security_events = (
        evidence.isolation_events
        + evidence.sandbox_escape_events
        + evidence.approval_bypass_events
    )
    regressions = {
        "quality_regression": evidence.quality_regression > policy.max_quality_regression,
        "cost_regression": evidence.cost_regression > policy.max_cost_regression,
        "p95_latency_regression": (
            evidence.p95_latency_regression > policy.max_p95_latency_regression
        ),
        "p99_latency_regression": (
            evidence.p99_latency_regression > policy.max_p99_latency_regression
        ),
        "policy_denial_rate": evidence.policy_denial_rate > policy.max_policy_denial_rate,
        "outbox_lag": evidence.outbox_lag_seconds > policy.max_outbox_lag_seconds,
        "lease_reclaim_rate": (
            evidence.lease_reclaim_rate > policy.max_lease_reclaim_rate
        ),
        "rollback_slo": (
            evidence.rollback_success_rate < policy.min_rollback_success_rate
        ),
    }
    breach_reasons = [name for name, breached in regressions.items() if breached]
    if security_events:
        breach_reasons.append("security_event")
    if breach_reasons:
        return RolloutDecision(
            decision="rollback",
            policy_version=policy.version,
            reasons=tuple(breach_reasons),
        )

    hold_reasons: list[str] = []
    if evidence.sample_size < policy.minimum_samples:
        hold_reasons.append("minimum_sample_size")
    if evidence.observed_hours < policy.cohort_observation_hours:
        hold_reasons.append("observation_window")
    if hold_reasons:
        return RolloutDecision(
            decision="hold", policy_version=policy.version, reasons=tuple(hold_reasons)
        )
    return RolloutDecision(
        decision="promote", policy_version=policy.version, reasons=("all_gates_passed",)
    )


__all__ = ["CanaryEvidence", "CertificationPolicy", "RolloutDecision", "decide_rollout"]
