from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.rag.catalogue import (
    RAG_CAPABILITY_CATALOGUE,
    RAGRuntimeDependency,
    ReadinessContext,
)
from app.rag.certification import (
    DEPLOYMENT_PROFILES,
    RAGCertificationRunner,
    RAGCertificationScenario,
    build_report,
    resolve_delegation,
)
from app.rag.contracts import RAGStrategy


def test_six_deployment_profiles_are_explicit_and_side_effect_free() -> None:
    assert set(DEPLOYMENT_PROFILES) == {"core", "provider", "graph", "web", "colbert", "raft"}
    assert RAGRuntimeDependency.WEB in DEPLOYMENT_PROFILES["web"]
    assert RAGRuntimeDependency.RAFT_MODEL in DEPLOYMENT_PROFILES["raft"]


def test_delegation_closure_excludes_unavailable_children_and_fails_closed() -> None:
    readiness = ReadinessContext.all_available().without(RAGRuntimeDependency.WEB)
    assert RAGStrategy.WEB_AUGMENTED not in resolve_delegation(RAGStrategy.AGENTIC, readiness)
    unavailable = readiness.without(RAGRuntimeDependency.DATABASE)
    with pytest.raises(RuntimeError):
        resolve_delegation(RAGStrategy.MODULAR, unavailable)


def _scenarios(now: datetime) -> tuple[RAGCertificationScenario, ...]:
    return tuple(
        RAGCertificationScenario(
            strategy=strategy,
            adapter_version="1.0.0",
            execution_passed=True,
            replay_passed=True,
            duplicate_delivery_passed=True,
            cancellation_passed=True,
            timeout_passed=True,
            budget_passed=True,
            policy_passed=True,
            citations_verified=True,
            cost_usd=0.01,
            evidence_reference=f"test-evidence://{strategy.value}",
            observed_at=now,
        )
        for strategy in RAGStrategy
    )


def test_report_contains_exactly_18_sorted_strategies_and_rejects_stale_or_fake_live_evidence() -> (
    None
):
    now = datetime.now(UTC)
    report = build_report(_scenarios(now), environment="test", live=False, now=now)
    assert len(report.results) == 18
    assert [item.strategy.value for item in report.results] == sorted(
        strategy.value for strategy in RAGStrategy
    )
    with pytest.raises(ValueError, match="stale"):
        build_report(_scenarios(now - timedelta(days=2)), environment="test", live=False, now=now)
    with pytest.raises(ValueError, match="synthetic"):
        build_report(_scenarios(now), environment="prod", live=True, now=now)


async def test_runner_covers_all_strategies_and_fails_closed_on_missing_checks() -> None:
    async def probe(strategy: RAGStrategy) -> dict[str, object]:
        return {
            "adapter_version": "1.0.0",
            "execution_passed": strategy is RAGStrategy.NAIVE,
            "evidence_reference": f"test-evidence://{strategy.value}",
        }

    report = await RAGCertificationRunner(probe).run_all(
        environment="test", live=False
    )
    assert len(report.results) == 18
    naive = next(item for item in report.results if item.strategy is RAGStrategy.NAIVE)
    assert naive.passed is False
def test_all_catalogue_entries_have_semantic_adapter_versions() -> None:
    assert {
        capability.adapter_version for capability in RAG_CAPABILITY_CATALOGUE.values()
    } == {"1.0.0"}
