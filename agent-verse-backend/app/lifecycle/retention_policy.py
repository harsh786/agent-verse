"""Data categories named by the deletion orchestrator.

This module also held an in-process ``RetentionPolicy`` tier table, plus
``ArchivePolicy`` / ``ExportPolicy`` / ``LegalHoldPolicy`` siblings, that no
code ever consulted (a10-F245-01). Retention is enforced by the maintenance
beat tasks against each store's own retention settings, legal holds live in the
``legal_holds`` table (checked by the deletion orchestrator), and exports are
tenant-scoped in their routes. The dead classes were removed so they cannot be
mistaken for the controls.
"""

from __future__ import annotations

import enum


class DataCategory(enum.StrEnum):
    GOAL_ARTIFACT = "goal_artifact"
    AUDIT_LOG = "audit_log"
    MEMORY = "memory"
    EMBEDDING = "embedding"
    KNOWLEDGE = "knowledge"
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    PII_DATA = "pii_data"
    PHI_DATA = "phi_data"
