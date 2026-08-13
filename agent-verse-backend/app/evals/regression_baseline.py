"""Immutable, version-qualified regression baselines."""

from __future__ import annotations

from dataclasses import dataclass

# Key type used by BaselineRepository
type _RepositoryKey = tuple[str, str, str, str, int, str, str, str]


@dataclass(frozen=True, slots=True)
class BaselineKey:
    tenant_id: str
    cohort: str
    strategy_id: str
    strategy_version: str
    profile_version: int
    evaluator_version: str
    eval_suite_version: str
    limits_policy_version: str

    @property
    def identity(self) -> tuple[str, str, str, str, int, str, str, str]:
        return (
            self.tenant_id,
            self.cohort,
            self.strategy_id,
            self.strategy_version,
            self.profile_version,
            self.evaluator_version,
            self.eval_suite_version,
            self.limits_policy_version,
        )


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    quality: float
    safety: float
    mean_cost_usd: float
    p95_latency_ms: float
    coverage: float
    sample_size: int
    policy_passed: bool
    tenant_isolation_passed: bool


@dataclass(frozen=True, slots=True)
class RegressionBaseline:
    key: BaselineKey
    revision: int
    metrics: AggregateMetrics

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("baseline revision must be positive")


class BaselineRepository:
    """Small append-only repository; PostgreSQL adapters use the same contract."""

    def __init__(self) -> None:
        self._items: dict[_RepositoryKey, list[RegressionBaseline]] = {}

    def append(self, baseline: RegressionBaseline) -> None:
        revisions = self._items.setdefault(baseline.key.identity, [])
        if any(item.revision == baseline.revision for item in revisions):
            raise ValueError("baseline revision already exists")
        if revisions and baseline.revision <= revisions[-1].revision:
            raise ValueError("baseline revisions must increase monotonically")
        revisions.append(baseline)

    def revisions(self, key: BaselineKey) -> tuple[RegressionBaseline, ...]:
        return tuple(self._items.get(key.identity, ()))

    def latest(self, key: BaselineKey) -> RegressionBaseline | None:
        revisions = self._items.get(key.identity, ())
        return revisions[-1] if revisions else None
