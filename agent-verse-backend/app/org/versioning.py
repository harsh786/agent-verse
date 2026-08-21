"""SUPPLEMENT L — Versioning Strategy for all entities.

Versioned entities:
  agents     — semantic versioning, history retained
  roles      — semantic, immutable after deploy
  prompts    — hash-based, A/B test enabled
  models     — provider-versioned, fallback chain
  workflows  — semantic, replay-safe
  policies   — semantic, full audit trail
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


class VersionStrategy(str, Enum):
    SEMANTIC = "semantic"  # MAJOR.MINOR.PATCH
    HASH = "hash"  # SHA256 of content
    PROVIDER = "provider"  # provider-specific versioning
    TIMESTAMP = "timestamp"  # datetime-based


@dataclass
class EntityVersion:
    """A versioned snapshot of any org entity."""

    entity_type: str  # agent | role | prompt | model | workflow | policy
    entity_id: str
    version: str  # e.g. "1.2.0" or "sha256:abc123"
    strategy: VersionStrategy
    content_hash: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_by: str = "system"
    deployed: bool = False
    deprecated: bool = False
    fallback_version: str | None = None
    ab_test_enabled: bool = False
    ab_test_traffic_pct: float = 0.0  # 0-1, portion of traffic to this version
    notes: str = ""

    @property
    def is_stable(self) -> bool:
        return self.deployed and not self.deprecated


@dataclass
class VersioningConfig:
    """Per-entity-type versioning configuration."""

    entity_type: str
    strategy: VersionStrategy
    history_enabled: bool = True
    immutable_after_deploy: bool = False
    ab_test_enabled: bool = False
    replay_safe: bool = False
    audit_trail: bool = True


# ── Entity versioning configurations (SUPPLEMENT L) ──────────────────────────

VERSIONED_ENTITIES: dict[str, VersioningConfig] = {
    "agents": VersioningConfig(
        "agents",
        VersionStrategy.SEMANTIC,
        history_enabled=True,
        immutable_after_deploy=False,
    ),
    "roles": VersioningConfig(
        "roles",
        VersionStrategy.SEMANTIC,
        history_enabled=True,
        immutable_after_deploy=True,
    ),
    "prompts": VersioningConfig(
        "prompts",
        VersionStrategy.HASH,
        history_enabled=True,
        ab_test_enabled=True,
    ),
    "models": VersioningConfig(
        "models",
        VersionStrategy.PROVIDER,
        history_enabled=True,
        immutable_after_deploy=False,
    ),
    "workflows": VersioningConfig(
        "workflows",
        VersionStrategy.SEMANTIC,
        history_enabled=True,
        replay_safe=True,
    ),
    "policies": VersioningConfig(
        "policies",
        VersionStrategy.SEMANTIC,
        history_enabled=True,
        audit_trail=True,
        immutable_after_deploy=False,
    ),
    "knowledge_collections": VersioningConfig(
        "knowledge_collections",
        VersionStrategy.TIMESTAMP,
        history_enabled=True,
    ),
    "capability_registries": VersioningConfig(
        "capability_registries",
        VersionStrategy.SEMANTIC,
        history_enabled=True,
    ),
}


class EntityVersionManager:
    """
    Manages versions for all org entities.
    In production: backed by DB table with full history.
    Development: in-memory.
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[EntityVersion]] = {}  # entity_id → history

    def _config(self, entity_type: str) -> VersioningConfig:
        return VERSIONED_ENTITIES.get(
            entity_type, VersioningConfig(entity_type, VersionStrategy.SEMANTIC)
        )

    def create_version(
        self,
        entity_type: str,
        entity_id: str,
        content: dict[str, Any],
        version_hint: str | None = None,
        created_by: str = "system",
        notes: str = "",
    ) -> EntityVersion:
        """Create a new version for an entity."""
        config = self._config(entity_type)
        content_hash = hashlib.sha256(str(content).encode()).hexdigest()[:16]

        if config.strategy == VersionStrategy.HASH:
            version = f"sha256:{content_hash}"
        elif config.strategy == VersionStrategy.TIMESTAMP:
            version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        else:
            # Semantic: auto-increment PATCH if hint not provided
            existing = self._versions.get(entity_id, [])
            if existing and version_hint is None:
                last = existing[-1].version
                try:
                    parts = last.split(".")
                    parts[-1] = str(int(parts[-1]) + 1)
                    version = ".".join(parts)
                except Exception:
                    version = version_hint or "1.0.0"
            else:
                version = version_hint or "1.0.0"

        ev = EntityVersion(
            entity_type=entity_type,
            entity_id=entity_id,
            version=version,
            strategy=config.strategy,
            content_hash=content_hash,
            created_by=created_by,
            ab_test_enabled=config.ab_test_enabled,
            notes=notes,
        )

        self._versions.setdefault(entity_id, []).append(ev)
        _log.info(
            "versioning.created",
            entity_type=entity_type,
            entity_id=entity_id,
            version=version,
        )
        return ev

    def deploy(self, entity_id: str, version: str) -> bool:
        """Mark a version as deployed."""
        for ev in self._versions.get(entity_id, []):
            if ev.version == version:
                config = self._config(ev.entity_type)
                if config.immutable_after_deploy and ev.deployed:
                    _log.warning("versioning.immutable_deployed", entity_id=entity_id)
                    return False
                ev.deployed = True
                _log.info("versioning.deployed", entity_id=entity_id, version=version)
                return True
        return False

    def deprecate(self, entity_id: str, version: str) -> bool:
        for ev in self._versions.get(entity_id, []):
            if ev.version == version:
                ev.deprecated = True
                return True
        return False

    def get_current(self, entity_id: str) -> EntityVersion | None:
        """Return the latest deployed non-deprecated version."""
        versions = self._versions.get(entity_id, [])
        deployed = [v for v in versions if v.is_stable]
        return deployed[-1] if deployed else None

    def list_versions(self, entity_id: str) -> list[EntityVersion]:
        return self._versions.get(entity_id, [])

    def get_ab_variants(self, entity_id: str) -> list[EntityVersion]:
        """Return all A/B test variants for an entity."""
        return [v for v in self._versions.get(entity_id, []) if v.ab_test_enabled and v.deployed]

    def select_ab_version(self, entity_id: str, request_hash: int) -> EntityVersion | None:
        """Select A/B variant based on request hash for deterministic routing."""
        variants = self.get_ab_variants(entity_id)
        if not variants:
            return self.get_current(entity_id)
        # Route based on hash modulo
        bucket = (request_hash % 100) / 100.0
        cumulative = 0.0
        for v in variants:
            cumulative += v.ab_test_traffic_pct
            if bucket < cumulative:
                return v
        return self.get_current(entity_id)


# Global singleton
entity_version_manager = EntityVersionManager()
