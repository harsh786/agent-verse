from datetime import UTC, datetime, timedelta

from app.evals.certification_policy import CanaryEvidence, CertificationPolicy, decide_rollout


def evidence(**overrides: object) -> CanaryEvidence:
    values = {
        "pattern_id": "react", "adapter_version": "2.0.0", "sample_size": 500,
        "quality_regression": 0.01, "cost_regression": 0.02,
        "p95_latency_regression": 0.03, "p99_latency_regression": 0.04,
        "policy_denial_rate": 0.01, "isolation_events": 0, "sandbox_escape_events": 0,
        "approval_bypass_events": 0, "outbox_lag_seconds": 1.0,
        "lease_reclaim_rate": 0.01, "rollback_success_rate": 1.0,
        "observed_hours": 72, "captured_at": datetime.now(UTC),
        "signed": True, "baseline_digest": "sha256:baseline", "evidence_digest": "sha256:evidence",
    }
    values.update(overrides)
    return CanaryEvidence.model_validate(values)


def test_policy_holds_missing_stale_or_unsigned_evidence() -> None:
    policy = CertificationPolicy()
    assert decide_rollout(policy, None).decision == "hold"
    assert decide_rollout(policy, evidence(signed=False)).decision == "hold"
    stale = evidence(captured_at=datetime.now(UTC) - timedelta(days=8))
    assert decide_rollout(policy, stale).decision == "hold"


def test_policy_promotes_only_after_quantitative_window() -> None:
    result = decide_rollout(CertificationPolicy(), evidence())
    assert result.decision == "promote"
    assert result.policy_version == "agent-pattern-rollout-v1"


def test_security_or_quality_breach_rolls_back() -> None:
    security = decide_rollout(CertificationPolicy(), evidence(isolation_events=1))
    quality = decide_rollout(CertificationPolicy(), evidence(quality_regression=0.2))
    assert security.decision == "rollback"
    assert quality.decision == "rollback"
