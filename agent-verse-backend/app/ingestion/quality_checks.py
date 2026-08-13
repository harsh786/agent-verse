"""QualityChecker — validates chunks before ingestion."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_NOISE_PATTERN = re.compile(r"^[\s\.\-_=+*#@!?/\\|<>(){}\[\]]+$")


@dataclass
class QualityCheckResult:
    passed: bool
    quality_score: float
    reason: str = ""


class QualityChecker:
    def __init__(self, min_length: int = 10, max_noise_ratio: float = 0.5) -> None:
        self._min_len = min_length
        self._max_noise = max_noise_ratio

    def check(self, content: str) -> QualityCheckResult:
        if not content or not content.strip():
            return QualityCheckResult(False, 0.0, "empty content")
        if len(content.strip()) < self._min_len:
            return QualityCheckResult(False, 0.1, f"too short (min {self._min_len} chars)")
        if _NOISE_PATTERN.match(content.strip()):
            return QualityCheckResult(False, 0.0, "noise-only content")
        words = re.findall(r"\b\w{3,}\b", content)
        word_chars = sum(len(w) for w in words)
        quality = min(1.0, word_chars / max(len(content), 1) * 1.5)
        passed = quality > self._max_noise
        return QualityCheckResult(
            passed,
            round(quality, 3),
            reason="" if passed else "low quality content",
        )


@dataclass
class DeduplicationResult:
    unique_chunks: list[str]
    duplicate_count: int
    seen_hashes: set[str] = field(default_factory=set)


class ContentDeduplicator:
    """Session-scoped chunk deduplicator using SHA-256 content hashes.

    Eliminates duplicate text chunks within a single ingestion batch.
    For cross-batch dedup, pass in a pre-seeded *seen_hashes* set.
    """

    def __init__(self, seen_hashes: set[str] | None = None) -> None:
        self._seen: set[str] = seen_hashes or set()

    @staticmethod
    def hash_chunk(content: str) -> str:
        """Return the SHA-256 hex digest of the normalised chunk content."""
        normalised = content.strip()
        return hashlib.sha256(normalised.encode()).hexdigest()

    def deduplicate(self, chunks: list[str]) -> DeduplicationResult:
        """Filter *chunks* to only those whose hash has not been seen before."""
        unique: list[str] = []
        dupe_count = 0
        for chunk in chunks:
            h = self.hash_chunk(chunk)
            if h in self._seen:
                dupe_count += 1
            else:
                self._seen.add(h)
                unique.append(chunk)
        return DeduplicationResult(
            unique_chunks=unique,
            duplicate_count=dupe_count,
            seen_hashes=self._seen,
        )

    def is_duplicate(self, content: str) -> bool:
        """Return True if this exact content has already been processed."""
        return self.hash_chunk(content) in self._seen
