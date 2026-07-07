"""EmbeddingDriftMonitor — monitors cosine similarity drift."""
from __future__ import annotations
import enum


class DriftSeverity(str, enum.Enum):
    STABLE = "stable"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EmbeddingDriftMonitor:
    def measure(self, avg_similarity: float, sample_size: int = 100) -> DriftSeverity:
        if avg_similarity >= 0.85:
            return DriftSeverity.STABLE
        elif avg_similarity >= 0.70:
            return DriftSeverity.LOW
        elif avg_similarity >= 0.55:
            return DriftSeverity.MEDIUM
        elif avg_similarity >= 0.40:
            return DriftSeverity.HIGH
        else:
            return DriftSeverity.CRITICAL

    def drift_score(self, avg_similarity: float) -> float:
        return max(0.0, min(1.0, 1.0 - avg_similarity))
