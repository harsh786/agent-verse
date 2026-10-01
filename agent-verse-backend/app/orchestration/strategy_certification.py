"""Evidence-derived lifecycle state for executable strategy capabilities."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.orchestration.strategy_contracts import CertificationKind
from app.orchestration.strategy_registry import StrategyCapability, StrategyState

EvidenceCategory = Literal[
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
]

REQUIRED_CERTIFICATION_CATEGORIES = frozenset(
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
)

CERTIFICATION_KIND_CATEGORIES: dict[CertificationKind, EvidenceCategory] = {
    CertificationKind.UNIT: "unit",
    CertificationKind.INTEGRATION: "integration",
    CertificationKind.RESTART: "restart_resume",
    CertificationKind.POLICY: "authorization_policy",
    CertificationKind.SECURITY: "tenant_isolation",
    CertificationKind.COST: "budget_timeout_cancellation",
    CertificationKind.LOAD: "load",
    CertificationKind.CANARY: "canary",
}


class RuntimeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: EvidenceCategory
    adapter_version: str
    passed: bool
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class CertificationDecision:
    state: StrategyState
    certified: bool
    missing_or_invalid_categories: tuple[str, ...]


class CertificationEvaluator:
    def __init__(
        self,
        *,
        max_age: timedelta = timedelta(days=30),
    ) -> None:
        if max_age.total_seconds() <= 0:
            raise ValueError("max_age must be positive")
        self._max_age = max_age

    def derive_state(
        self,
        capability: StrategyCapability,
        evidence: Iterable[RuntimeEvidence],
        *,
        now: datetime | None = None,
    ) -> CertificationDecision:
        if capability.state is StrategyState.DISABLED:
            return CertificationDecision(StrategyState.DISABLED, False, ())
        if capability.adapter_descriptor is None:
            return CertificationDecision(StrategyState.PARTIAL, False, ())

        checked_at = now or datetime.now(UTC)
        valid_categories = {
            item.category
            for item in evidence
            if item.passed
            and item.adapter_version == capability.adapter_version
            and item.recorded_at.tzinfo is not None
            and checked_at - item.recorded_at <= self._max_age
        }
        missing = tuple(sorted(REQUIRED_CERTIFICATION_CATEGORIES - valid_categories))
        if not missing:
            return CertificationDecision(StrategyState.CERTIFIED, True, ())
        if "integration" in valid_categories:
            return CertificationDecision(StrategyState.IMPLEMENTED, False, missing)
        return CertificationDecision(StrategyState.PARTIAL, False, missing)


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    path: Literal["legacy", "v2", "rejected"]
    shadow_comparison: dict[str, Any]


class RolloutController:
    def __init__(
        self,
        *,
        shadow: bool = False,
        allowlist: frozenset[str] = frozenset(),
        kill_switch: bool = False,
    ) -> None:
        self._shadow = shadow
        self._allowlist = allowlist
        self._kill_switch = kill_switch

    def choose(
        self,
        tenant_id: str,
        legacy: dict[str, Any],
        version_v2: dict[str, Any],
        *,
        already_admitted: bool = False,
    ) -> RolloutDecision:
        comparison = {
            "legacy": legacy,
            "version_v2": version_v2,
            "strategy_mismatch": legacy.get("strategy") != version_v2.get("strategy"),
            "topology_mismatch": legacy.get("topology") != version_v2.get("topology"),
            "readiness_mismatch": legacy.get("readiness") != version_v2.get("readiness"),
            "cost_mismatch": legacy.get("cost") != version_v2.get("cost"),
            "latency_mismatch": legacy.get("latency") != version_v2.get("latency"),
        }
        if self._shadow:
            return RolloutDecision("legacy", comparison)
        # "*" admits every tenant (STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST=*).
        if "*" not in self._allowlist and tenant_id not in self._allowlist:
            return RolloutDecision("legacy", comparison)
        if self._kill_switch and not already_admitted:
            return RolloutDecision("rejected", comparison)
        return RolloutDecision("v2", comparison)


class StrategyEvidenceStore:
    current_query = """
        SELECT * FROM strategy_certification_evidence
        WHERE tenant_id = :tenant_id
          AND strategy_id = :strategy_id
          AND adapter_version = :adapter_version
          AND state_schema_version = :state_schema_version
          AND expires_at > :now
        ORDER BY observed_at DESC
        LIMIT 500
    """

    # Newest N rows PER STRATEGY (window function), not a single LIMIT over the
    # tenant: a hot strategy's rows used to crowd quiet strategies out of the
    # catalogue entirely (CORE-17). Served by ix_strategy_evidence_recent.
    tenant_current_query = """
        SELECT * FROM (
            SELECT e.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY e.strategy_id ORDER BY e.observed_at DESC
                   ) AS evidence_rank
            FROM strategy_certification_evidence e
            WHERE e.tenant_id = :tenant_id
              AND e.expires_at > :now
        ) ranked
        WHERE ranked.evidence_rank <= :per_strategy
        ORDER BY ranked.observed_at DESC
    """

    purge_batch_query = """
        DELETE FROM strategy_certification_evidence
        WHERE id IN (
            SELECT id FROM strategy_certification_evidence
            WHERE expires_at <= :now
            ORDER BY expires_at
            LIMIT :batch_size
        )
    """

    def __init__(self, db_session_factory: Any) -> None:
        self._db = db_session_factory

    async def list_current_for_tenant(
        self, *, tenant_id: str, now: datetime | None = None, per_strategy: int = 200
    ) -> list[dict[str, Any]]:
        """A tenant's unexpired evidence, newest ``per_strategy`` rows of each strategy."""
        if self._db is None:
            return []
        from sqlalchemy import text

        checked_at = now or datetime.now(UTC)
        async with self._db() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            result = await session.execute(
                text(self.tenant_current_query),
                {
                    "tenant_id": tenant_id,
                    "now": checked_at,
                    "per_strategy": max(1, int(per_strategy)),
                },
            )
            return [dict(row) for row in result.mappings().all()]

    async def purge_expired(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = 1000,
        max_batches: int = 100,
    ) -> int:
        """Delete expired evidence across tenants in bounded batches; returns rows deleted.

        Nothing deleted expired rows, so the table grew forever. This is SYSTEM
        work: the factory must be the maintenance (BYPASSRLS) one and each batch
        runs under ``system_session`` — under the NOBYPASSRLS application role it
        fails loudly instead of silently deleting nothing.
        """
        if self._db is None:
            return 0
        cutoff = now or datetime.now(UTC)
        total = 0
        for _ in range(max(1, int(max_batches))):
            deleted = await self._purge_batch(cutoff, batch_size)
            total += deleted
            if deleted < batch_size:
                break
        return total

    async def purge_until_drained(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = 5000,
        time_budget_s: float = 240.0,
        clock: Any = None,
    ) -> tuple[int, bool]:
        """Delete expired evidence until a batch comes back short; ``(deleted, drained)``.

        CORE-36: a fixed batch cap (100k rows/day) fell behind one-row-per-goal
        write volume at scale. This keeps deleting committed, index-ordered
        batches until the backlog is gone, bounded by *time_budget_s* so one run
        cannot hold a maintenance worker forever; ``drained=False`` tells the
        caller to continue in a fresh run. Raises on a DB error.
        """
        import time

        tick = clock or time.monotonic
        cutoff = now or datetime.now(UTC)
        deadline = tick() + max(0.0, float(time_budget_s))
        total = 0
        while True:
            deleted = await self._purge_batch(cutoff, batch_size)
            total += deleted
            if deleted < batch_size:
                return total, True
            if tick() >= deadline:
                return total, False

    async def _purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """One committed DELETE of at most *batch_size* expired rows (system role)."""
        from sqlalchemy import text

        from app.db.rls import system_session

        async with self._db() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(self.purge_batch_query),
                {"now": cutoff, "batch_size": max(1, int(batch_size))},
            )
            return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def is_current(expires_at: datetime, now: datetime) -> bool:
        if expires_at.tzinfo is None or now.tzinfo is None:
            raise ValueError("evidence timestamps must be timezone-aware")
        return expires_at > now

    async def append(
        self,
        *,
        tenant_id: str,
        strategy_id: str,
        adapter_version: str,
        state_schema_version: int,
        evidence_type: str,
        result: str,
        artifact_reference: str,
        observed_at: datetime,
        expires_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> str:
        if self._db is None:
            raise RuntimeError("database session factory is required")
        if not self.is_current(expires_at, observed_at):
            raise ValueError("evidence expiry must follow observation")
        from sqlalchemy import text

        evidence_id = uuid.uuid4().hex
        async with self._db() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO strategy_certification_evidence
                    (id, tenant_id, strategy_id, adapter_version, state_schema_version,
                     evidence_type, result, artifact_reference, observed_at, expires_at, details)
                    VALUES (:id, :tenant_id, :strategy_id, :adapter_version,
                            :state_schema_version, :evidence_type, :result,
                            :artifact_reference, :observed_at, :expires_at,
                            CAST(:details AS jsonb))
                    """
                ),
                {
                    "id": evidence_id,
                    "tenant_id": tenant_id,
                    "strategy_id": strategy_id,
                    "adapter_version": adapter_version,
                    "state_schema_version": state_schema_version,
                    "evidence_type": evidence_type,
                    "result": result,
                    "artifact_reference": artifact_reference,
                    "observed_at": observed_at,
                    "expires_at": expires_at,
                    "details": __import__("json").dumps(details or {}),
                },
            )
        return evidence_id

    async def list_current(
        self,
        *,
        tenant_id: str,
        strategy_id: str,
        adapter_version: str,
        state_schema_version: int,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        from sqlalchemy import text

        checked_at = now or datetime.now(UTC)
        async with self._db() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            result = await session.execute(
                text(self.current_query),
                {
                    "tenant_id": tenant_id,
                    "strategy_id": strategy_id,
                    "adapter_version": adapter_version,
                    "state_schema_version": state_schema_version,
                    "now": checked_at,
                },
            )
            return [dict(row) for row in result.mappings().all()]
