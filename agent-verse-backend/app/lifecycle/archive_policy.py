from __future__ import annotations

from app.lifecycle.retention_policy import DataCategory, RetentionTier


class ArchivePolicy:
    def should_archive(self, category: DataCategory, age_days: int) -> bool:
        from app.lifecycle.retention_policy import RetentionPolicy

        policy = RetentionPolicy()
        tier = policy.get_tier(category)
        archive_after = {
            RetentionTier.SHORT: 30,
            RetentionTier.DEFAULT: 90,
            RetentionTier.LONG: 365,
            RetentionTier.REGULATED: 365 * 7,
            RetentionTier.LEGAL_HOLD: 999999,
        }
        return age_days >= archive_after.get(tier, 90)
