"""SUPP-J Decision Intelligence + SUPP-L Versioning Strategy.

Decision Intelligence (SUPP-J):
    Scores and explains every autonomous decision made by the org.
    Records decision quality, outcome, and confidence for learning.

Versioning Strategy (SUPP-L):
    Tracks version history of: org config, blueprints, capabilities,
    policy sets. Enables rollback and change audit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── SUPP-J: Decision Intelligence ────────────────────────────────────────────


@dataclass
class DecisionRecord:
    """A recorded autonomous decision with quality metadata."""

    decision_id: str
    org_id: str
    tenant_id: str
    decision_type: str  # 'mission_create' | 'team_form' | 'capability_assign' etc.
    description: str
    rationale: str
    confidence: float  # 0-1
    autonomy_level: int  # the L level that authorized this decision
    outcome: str = "pending"  # pending | accepted | rejected | succeeded | failed
    quality_score: float = 0.0
    made_at: str = ""
    resolved_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.made_at:
            self.made_at = datetime.now(UTC).isoformat()


class DecisionIntelligence:
    """SUPP-J — Quality assurance and explainability for autonomous decisions.

    Responsibilities:
    1. Record every decision with rationale + confidence
    2. Score decision quality post-outcome
    3. Surface patterns: high-confidence decisions that failed
    4. Feed outcomes back to improve future confidence calibration
    """

    def __init__(self) -> None:
        self._records: dict[str, list[DecisionRecord]] = {}  # org_id → [records]

    def record(self, decision: DecisionRecord) -> None:
        """Record a new autonomous decision."""
        with _tracer.start_as_current_span("decision_intelligence.record") as span:
            span.set_attribute("org_id", decision.org_id)
            span.set_attribute("decision_type", decision.decision_type)
            span.set_attribute("confidence", decision.confidence)

            self._records.setdefault(decision.org_id, []).append(decision)
            _log.info(
                "decision.recorded",
                decision_id=decision.decision_id,
                org_id=decision.org_id,
                decision_type=decision.decision_type,
                confidence=decision.confidence,
            )

    def resolve(self, org_id: str, decision_id: str, outcome: str) -> DecisionRecord | None:
        """Update outcome and compute quality score."""
        for rec in self._records.get(org_id, []):
            if rec.decision_id == decision_id:
                rec.outcome = outcome
                rec.resolved_at = datetime.now(UTC).isoformat()

                # Quality score: penalise confident decisions that failed
                if outcome in ("accepted", "succeeded"):
                    rec.quality_score = rec.confidence
                elif outcome in ("rejected", "failed"):
                    rec.quality_score = (
                        1.0 - rec.confidence
                    )  # inverse: confident but failed = low quality
                else:
                    rec.quality_score = 0.5

                _log.info(
                    "decision.resolved",
                    decision_id=decision_id,
                    outcome=outcome,
                    quality=rec.quality_score,
                )
                return rec
        return None

    def decision_quality_report(self, org_id: str) -> dict[str, Any]:
        """Aggregate quality metrics for an org's decisions."""
        records = self._records.get(org_id, [])
        if not records:
            return {"total": 0, "avg_confidence": 0.0, "avg_quality": 0.0, "pending": 0}

        resolved = [r for r in records if r.outcome != "pending"]
        avg_conf = sum(r.confidence for r in records) / len(records)
        avg_qual = sum(r.quality_score for r in resolved) / len(resolved) if resolved else 0.0

        # Identify high-confidence failures (calibration issue)
        hc_failures = [
            r for r in resolved if r.confidence > 0.8 and r.outcome in ("rejected", "failed")
        ]

        return {
            "total": len(records),
            "resolved": len(resolved),
            "pending": len(records) - len(resolved),
            "avg_confidence": round(avg_conf, 3),
            "avg_quality": round(avg_qual, 3),
            "high_confidence_failures": len(hc_failures),
            "calibration_warning": len(hc_failures) > 2,
        }

    def list_decisions(self, org_id: str, limit: int = 50) -> list[dict[str, Any]]:
        records = self._records.get(org_id, [])
        return [
            {
                "decision_id": r.decision_id,
                "decision_type": r.decision_type,
                "description": r.description,
                "confidence": r.confidence,
                "autonomy_level": r.autonomy_level,
                "outcome": r.outcome,
                "quality_score": r.quality_score,
                "made_at": r.made_at,
            }
            for r in sorted(records, key=lambda x: x.made_at, reverse=True)[:limit]
        ]


# ── SUPP-L: Versioning Strategy ───────────────────────────────────────────────


@dataclass
class VersionRecord:
    """A point-in-time snapshot of a versioned entity."""

    version_id: str
    entity_type: str  # 'org_config' | 'blueprint' | 'capability_set' | 'policy_set'
    entity_id: str
    tenant_id: str
    version_num: int
    content_hash: str
    snapshot: dict[str, Any]
    changed_by: str = "system"
    change_reason: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if not self.content_hash:
            raw = json.dumps(self.snapshot, sort_keys=True)
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]


class VersionStore:
    """SUPP-L — Version history for org config, blueprints, and policy sets.

    Supports:
    - Immutable version snapshots
    - Rollback to any previous version
    - Diff between two versions
    - Change audit trail
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[VersionRecord]] = {}  # entity_id → versions

    def _key(self, entity_type: str, entity_id: str) -> str:
        return f"{entity_type}:{entity_id}"

    def save(
        self,
        entity_type: str,
        entity_id: str,
        tenant_id: str,
        snapshot: dict[str, Any],
        changed_by: str = "system",
        change_reason: str = "",
    ) -> VersionRecord:
        """Save a new version snapshot."""
        with _tracer.start_as_current_span("version_store.save") as span:
            span.set_attribute("entity_type", entity_type)
            span.set_attribute("entity_id", entity_id)

            key = self._key(entity_type, entity_id)
            existing = self._versions.get(key, [])
            version_num = len(existing) + 1

            from uuid import uuid4

            rec = VersionRecord(
                version_id=str(uuid4()),
                entity_type=entity_type,
                entity_id=entity_id,
                tenant_id=tenant_id,
                version_num=version_num,
                content_hash="",  # computed in __post_init__
                snapshot=snapshot,
                changed_by=changed_by,
                change_reason=change_reason,
            )
            existing.append(rec)
            self._versions[key] = existing

            span.set_attribute("version_num", version_num)
            _log.info(
                "version.saved", entity_type=entity_type, entity_id=entity_id, version=version_num
            )
            return rec

    def history(self, entity_type: str, entity_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return version history newest-first."""
        key = self._key(entity_type, entity_id)
        records = self._versions.get(key, [])
        return [
            {
                "version_id": r.version_id,
                "version_num": r.version_num,
                "content_hash": r.content_hash,
                "changed_by": r.changed_by,
                "change_reason": r.change_reason,
                "created_at": r.created_at,
            }
            for r in sorted(records, key=lambda x: x.version_num, reverse=True)[:limit]
        ]

    def get_version(
        self, entity_type: str, entity_id: str, version_num: int
    ) -> VersionRecord | None:
        """Retrieve a specific version snapshot."""
        key = self._key(entity_type, entity_id)
        for r in self._versions.get(key, []):
            if r.version_num == version_num:
                return r
        return None

    def rollback(
        self,
        entity_type: str,
        entity_id: str,
        tenant_id: str,
        to_version: int,
        rolled_back_by: str = "system",
    ) -> VersionRecord | None:
        """Roll back to a previous version (creates a new version with old snapshot)."""
        target = self.get_version(entity_type, entity_id, to_version)
        if target is None:
            return None
        return self.save(
            entity_type=entity_type,
            entity_id=entity_id,
            tenant_id=tenant_id,
            snapshot=target.snapshot,
            changed_by=rolled_back_by,
            change_reason=f"Rollback to version {to_version}",
        )

    def diff(self, entity_type: str, entity_id: str, v_from: int, v_to: int) -> dict[str, Any]:
        """Return a simple diff between two versions."""
        r_from = self.get_version(entity_type, entity_id, v_from)
        r_to = self.get_version(entity_type, entity_id, v_to)
        if not r_from or not r_to:
            return {"error": "Version(s) not found"}

        added = {k: v for k, v in r_to.snapshot.items() if k not in r_from.snapshot}
        removed = {k: v for k, v in r_from.snapshot.items() if k not in r_to.snapshot}
        changed = {
            k: {"from": r_from.snapshot[k], "to": r_to.snapshot[k]}
            for k in r_from.snapshot
            if k in r_to.snapshot and r_from.snapshot[k] != r_to.snapshot[k]
        }

        return {
            "from_version": v_from,
            "to_version": v_to,
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged_count": len(r_from.snapshot) - len(removed) - len(changed),
        }


# ── Singletons ────────────────────────────────────────────────────────────────

_decision_intelligence = DecisionIntelligence()
_version_store = VersionStore()


def get_decision_intelligence() -> DecisionIntelligence:
    return _decision_intelligence


def get_version_store() -> VersionStore:
    return _version_store
