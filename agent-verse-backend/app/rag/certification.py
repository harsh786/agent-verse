"""Truthful readiness closure and deterministic certification for all 18 RAG strategies."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from app.rag.catalogue import (
    RAG_CAPABILITY_CATALOGUE,
    RAGRuntimeDependency,
    ReadinessContext,
)
from app.rag.contracts import RAGStrategy

CertificationProbe = Callable[
    [RAGStrategy], Awaitable[Mapping[str, bool | float | str]]
]
_BOOLEAN_CHECKS = (
    "execution_passed",
    "replay_passed",
    "duplicate_delivery_passed",
    "cancellation_passed",
    "timeout_passed",
    "budget_passed",
    "policy_passed",
    "citations_verified",
)

DEPLOYMENT_PROFILES = MappingProxyType(
    {
        "core": frozenset({RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER}),
        "provider": frozenset(
            {
                RAGRuntimeDependency.DATABASE,
                RAGRuntimeDependency.EMBEDDER,
                RAGRuntimeDependency.PROVIDER,
            }
        ),
        "graph": frozenset(
            {
                RAGRuntimeDependency.DATABASE,
                RAGRuntimeDependency.EMBEDDER,
                RAGRuntimeDependency.GRAPH,
            }
        ),
        "web": frozenset(
            {RAGRuntimeDependency.DATABASE, RAGRuntimeDependency.EMBEDDER, RAGRuntimeDependency.WEB}
        ),
        "colbert": frozenset(
            {
                RAGRuntimeDependency.DATABASE,
                RAGRuntimeDependency.EMBEDDER,
                RAGRuntimeDependency.COLBERT_LIBRARY,
                RAGRuntimeDependency.COLBERT_CHECKPOINT,
            }
        ),
        "raft": frozenset(
            {
                RAGRuntimeDependency.DATABASE,
                RAGRuntimeDependency.EMBEDDER,
                RAGRuntimeDependency.RAFT_SERVICE,
                RAGRuntimeDependency.RAFT_MODEL,
            }
        ),
    }
)

DELEGATION_CANDIDATES = MappingProxyType(
    {
        RAGStrategy.ADAPTIVE: (RAGStrategy.HYBRID, RAGStrategy.NAIVE),
        RAGStrategy.CORRECTIVE: (RAGStrategy.HYBRID, RAGStrategy.WEB_AUGMENTED),
        RAGStrategy.AGENTIC: (RAGStrategy.HYBRID, RAGStrategy.GRAPH, RAGStrategy.WEB_AUGMENTED),
        RAGStrategy.MODULAR: (RAGStrategy.HYBRID, RAGStrategy.GRAPH),
    }
)


def resolve_delegation(
    requested: RAGStrategy, readiness: ReadinessContext
) -> tuple[RAGStrategy, ...]:
    candidates = DELEGATION_CANDIDATES.get(requested, ())
    resolved = tuple(
        strategy
        for strategy in candidates
        if RAG_CAPABILITY_CATALOGUE[strategy].evaluate_readiness(readiness).available
    )
    if candidates and not resolved:
        raise RuntimeError(f"no ready delegated strategy for {requested.value}")
    return resolved


class RAGCertificationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: RAGStrategy
    adapter_version: str
    execution_passed: bool
    replay_passed: bool
    duplicate_delivery_passed: bool
    cancellation_passed: bool
    timeout_passed: bool
    budget_passed: bool
    policy_passed: bool
    citations_verified: bool
    cost_usd: float
    evidence_reference: str
    observed_at: datetime

    @property
    def passed(self) -> bool:
        checks = self.model_dump(
            exclude={"strategy", "adapter_version", "cost_usd", "evidence_reference", "observed_at"}
        )
        return all(bool(value) for value in checks.values())


class RAGCertificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    generated_at: datetime
    environment: str
    live: bool
    results: tuple[RAGCertificationScenario, ...]
    report_digest: str


def build_report(
    scenarios: tuple[RAGCertificationScenario, ...],
    *,
    environment: str,
    live: bool,
    now: datetime | None = None,
) -> RAGCertificationReport:
    strategies = {item.strategy for item in scenarios}
    if strategies != set(RAGStrategy) or len(scenarios) != len(RAGStrategy):
        raise ValueError("certification report must contain exactly all 18 RAG strategies")
    generated = now or datetime.now(UTC)
    for item in scenarios:
        if item.observed_at.tzinfo is None or generated - item.observed_at > timedelta(days=1):
            raise ValueError("stale or timezone-naive RAG certification evidence")
        if live and not item.evidence_reference.startswith("live-evidence://"):
            raise ValueError("live certification rejects synthetic evidence")
    ordered = tuple(sorted(scenarios, key=lambda item: item.strategy.value))
    payload = json.dumps(
        [item.model_dump(mode="json") for item in ordered], sort_keys=True, separators=(",", ":")
    )
    return RAGCertificationReport(
        generated_at=generated,
        environment=environment,
        live=live,
        results=ordered,
        report_digest=hashlib.sha256(payload.encode()).hexdigest(),
    )


def write_report(report: RAGCertificationReport, path: Path) -> None:
    path.write_text(report.model_dump_json(indent=2) + "\n")


def write_markdown_report(report: RAGCertificationReport, path: Path) -> None:
    rows = [
        "# RAG Strategy Certification",
        "",
        f"Environment: `{report.environment}`  ",
        f"Live evidence: `{'yes' if report.live else 'no'}`  ",
        f"Digest: `{report.report_digest}`",
        "",
        "| Strategy | Adapter | Result | Evidence |",
        "|---|---|---|---|",
    ]
    rows.extend(
        "| {strategy} | {version} | {result} | `{evidence}` |".format(
            strategy=item.strategy.value,
            version=item.adapter_version,
            result="PASS" if item.passed else "HOLD",
            evidence=item.evidence_reference,
        )
        for item in report.results
    )
    path.write_text("\n".join(rows) + "\n")


class RAGCertificationRunner:
    """Run the same fail-closed scenario contract for every canonical strategy."""

    def __init__(self, probe: CertificationProbe) -> None:
        self._probe = probe

    async def run_all(
        self,
        *,
        environment: str,
        live: bool,
        now: datetime | None = None,
    ) -> RAGCertificationReport:
        observed = now or datetime.now(UTC)
        results: list[RAGCertificationScenario] = []
        for strategy in sorted(RAGStrategy, key=lambda item: item.value):
            try:
                evidence = dict(await self._probe(strategy))
            except Exception:
                evidence = {}
            results.append(
                RAGCertificationScenario(
                    strategy=strategy,
                    adapter_version=str(evidence.get("adapter_version", "unknown")),
                    **{name: bool(evidence.get(name, False)) for name in _BOOLEAN_CHECKS},
                    cost_usd=float(evidence.get("cost_usd", 0.0)),
                    evidence_reference=str(
                        evidence.get(
                            "evidence_reference",
                            f"missing-evidence://{strategy.value}",
                        )
                    ),
                    observed_at=observed,
                )
            )
        return build_report(
            tuple(results), environment=environment, live=live, now=observed
        )


__all__ = [
    "DELEGATION_CANDIDATES",
    "DEPLOYMENT_PROFILES",
    "RAGCertificationReport",
    "RAGCertificationRunner",
    "RAGCertificationScenario",
    "build_report",
    "resolve_delegation",
    "write_markdown_report",
    "write_report",
]
