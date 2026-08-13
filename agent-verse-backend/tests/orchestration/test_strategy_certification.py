from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.orchestration.strategy_certification import (
    CERTIFICATION_KIND_CATEGORIES,
    REQUIRED_CERTIFICATION_CATEGORIES,
    CertificationEvaluator,
    RuntimeEvidence,
)
from app.orchestration.strategy_contracts import CertificationKind
from app.orchestration.strategy_registry import StrategyState, build_default_registry


def evidence_set(*, version: str = "1.0.0", stale: bool = False) -> tuple[RuntimeEvidence, ...]:
    recorded_at = datetime.now(UTC) - (timedelta(days=31) if stale else timedelta())
    return tuple(
        RuntimeEvidence(
            category=category,
            adapter_version=version,
            passed=True,
            recorded_at=recorded_at,
        )
        for category in REQUIRED_CERTIFICATION_CATEGORIES
    )


def test_required_certification_categories_cover_operational_gate() -> None:
    assert frozenset(
        {
            "unit",
            "integration",
            "restart_resume",
            "duplicate_delivery",
            "tenant_isolation",
            "authorization_policy",
            "budget_timeout_cancellation",
            "observability_explainability",
            "load",
            "canary",
        }
    ) == REQUIRED_CERTIFICATION_CATEGORIES


def test_contract_enum_remains_compatible_with_persisted_evidence_kinds() -> None:
    assert set(CERTIFICATION_KIND_CATEGORIES) == set(CertificationKind)
    assert set(CERTIFICATION_KIND_CATEGORIES.values()) <= REQUIRED_CERTIFICATION_CATEGORIES


def test_executable_strategy_with_production_path_evidence_is_implemented() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    evaluator = CertificationEvaluator(max_age=timedelta(days=30))
    evidence = (
        RuntimeEvidence(
            category="integration",
            adapter_version=capability.adapter_version,
            passed=True,
            recorded_at=datetime.now(UTC),
        ),
    )

    decision = evaluator.derive_state(capability, evidence)

    assert decision.state is StrategyState.IMPLEMENTED
    assert not decision.certified


def test_all_fresh_matching_evidence_is_required_for_certification() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    evaluator = CertificationEvaluator(max_age=timedelta(days=30))

    assert evaluator.derive_state(capability, evidence_set()).state is StrategyState.CERTIFIED
    stale = evaluator.derive_state(capability, evidence_set(stale=True))
    mismatched = evaluator.derive_state(capability, evidence_set(version="2.0.0"))
    assert stale.state is StrategyState.PARTIAL
    assert mismatched.state is StrategyState.PARTIAL


def test_failed_or_missing_evidence_cannot_overstate_registry_state() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    evidence = list(evidence_set())
    canary_index = next(
        index for index, item in enumerate(evidence) if item.category == "canary"
    )
    evidence[canary_index] = evidence[canary_index].model_copy(update={"passed": False})

    decision = CertificationEvaluator().derive_state(capability, tuple(evidence))

    assert decision.state is StrategyState.IMPLEMENTED
    assert "canary" in decision.missing_or_invalid_categories


@pytest.mark.parametrize("strategy_id", ["loop_engineering"])
def test_non_executable_or_disabled_strategy_cannot_be_promoted(strategy_id: str) -> None:
    capability = build_default_registry().get(strategy_id)
    assert capability is not None

    decision = CertificationEvaluator().derive_state(capability, evidence_set())

    expected = (
        StrategyState.DISABLED
        if capability.state is StrategyState.DISABLED
        else StrategyState.PARTIAL
    )
    assert decision.state is expected
