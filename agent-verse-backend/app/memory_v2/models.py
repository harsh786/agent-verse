"""Agent Memory 2.0 - provenance, lifecycle, conflict detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MemoryLifecycleState(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    DISPUTED = "disputed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemoryPrivacyClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PII = "pii"
    PHI = "phi"


@dataclass
class MemoryProvenance:
    """Provenance tracking for a memory entry."""

    created_from_goal_id: str | None = None
    created_from_tool: str | None = None
    extracted_from_document: str | None = None
    confidence_evidence: str = ""
    last_updated_by: str | None = None
    update_count: int = 0


@dataclass
class MemoryConflict:
    """A detected conflict between two memory entries."""

    conflict_id: str
    tenant_id: str
    memory_id_a: str
    memory_id_b: str
    conflict_description: str
    severity: str = "low"
    resolution: str | None = None
    resolved: bool = False
    created_at: str | None = None
