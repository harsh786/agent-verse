from __future__ import annotations

import pytest

from app.coordination.moa.diversity_policy import DiversityPolicy
from app.coordination.moa.models import ModelCandidate


def _candidate(
    name: str, provider: str, family: str, deployment: str, domain: str
) -> ModelCandidate:
    return ModelCandidate(
        candidate_id=name,
        provider_id=provider,
        model_family=family,
        deployment_id=deployment,
        region="in",
        failure_domain=domain,
        healthy=True,
        context_limit=32_000,
        estimated_cost_usd=0.1,
        estimated_latency_ms=100,
        capabilities=frozenset({"reasoning"}),
    )


def test_diversity_dedupes_alias_deployments_and_failure_domains() -> None:
    candidates = (
        _candidate("a", "p1", "f1", "deployment-1", "d1"),
        _candidate("alias-a", "p1", "f1", "deployment-1", "d1"),
        _candidate("b", "p2", "f2", "deployment-2", "d2"),
    )
    selected = DiversityPolicy(
        minimum_providers=2,
        minimum_model_families=2,
        minimum_failure_domains=2,
    ).select(candidates, count=2, region="in", required_capabilities=frozenset({"reasoning"}))
    assert [item.deployment_id for item in selected] == ["deployment-1", "deployment-2"]


def test_diversity_fails_closed_for_health_region_and_insufficient_independence() -> None:
    unhealthy = _candidate("a", "p1", "f1", "d1", "domain").model_copy(update={"healthy": False})
    wrong_region = _candidate("b", "p2", "f2", "d2", "domain").model_copy(update={"region": "us"})
    with pytest.raises(ValueError, match="diversity"):
        DiversityPolicy(2, 2, 2).select(
            (unhealthy, wrong_region),
            count=2,
            region="in",
            required_capabilities=frozenset({"reasoning"}),
        )
