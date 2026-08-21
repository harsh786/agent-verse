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


class RetentionTier(enum.StrEnum):
    SHORT = "short"
    DEFAULT = "default"
    LONG = "long"
    REGULATED = "regulated"
    LEGAL_HOLD = "legal_hold"


_POLICY: dict[DataCategory, RetentionTier] = {
    DataCategory.GOAL_ARTIFACT: RetentionTier.DEFAULT,
    DataCategory.AUDIT_LOG: RetentionTier.LONG,
    DataCategory.MEMORY: RetentionTier.SHORT,
    DataCategory.EMBEDDING: RetentionTier.DEFAULT,
    DataCategory.KNOWLEDGE: RetentionTier.DEFAULT,
    DataCategory.SCREENSHOT: RetentionTier.SHORT,
    DataCategory.VIDEO: RetentionTier.SHORT,
    DataCategory.PII_DATA: RetentionTier.REGULATED,
    DataCategory.PHI_DATA: RetentionTier.REGULATED,
}


class RetentionPolicy:
    def get_tier(self, category: DataCategory) -> RetentionTier:
        return _POLICY.get(category, RetentionTier.DEFAULT)
