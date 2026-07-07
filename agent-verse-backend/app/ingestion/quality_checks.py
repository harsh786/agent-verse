"""QualityChecker — validates chunks before ingestion."""
from __future__ import annotations
import re
from dataclasses import dataclass

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
