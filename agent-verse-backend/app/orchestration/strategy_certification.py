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
        if tenant_id not in self._allowlist:
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
    """

    def __init__(self, db_session_factory: Any) -> None:
        self._db = db_session_factory

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
