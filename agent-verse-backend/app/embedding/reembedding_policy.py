"""ReembeddingPolicy — decides when to re-embed a collection."""
from __future__ import annotations
import enum


class ReembeddingTrigger(str, enum.Enum):
    NONE = "none"
    MODEL_CHANGED = "model_changed"
    DRIFT_DETECTED = "drift_detected"
    STALE = "stale"
    DIMENSION_MISMATCH = "dimension_mismatch"


_DRIFT_THRESHOLD = 0.25


class ReembeddingPolicy:
    def should_reembed(
        self,
        current_model: str,
        new_model: str,
        collection_size: int,
        drift_score: float = 0.0,
        age_days: int = 0,
        staleness_threshold_days: int = 90,
        old_dim: int | None = None,
        new_dim: int | None = None,
    ) -> ReembeddingTrigger:
        if current_model != new_model:
            return ReembeddingTrigger.MODEL_CHANGED
        if old_dim is not None and new_dim is not None and old_dim != new_dim:
            return ReembeddingTrigger.DIMENSION_MISMATCH
        if drift_score > _DRIFT_THRESHOLD:
            return ReembeddingTrigger.DRIFT_DETECTED
        if age_days > staleness_threshold_days and collection_size > 100:
            return ReembeddingTrigger.STALE
        return ReembeddingTrigger.NONE
