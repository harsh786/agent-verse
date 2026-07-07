"""FallbackChain — tracks fallback attempts per doc-2 §4 exact order."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FallbackAttempt:
    source: str
    success: bool
    reason: str


class FallbackChain:
    # Doc-2 §4: exact fallback order
    FALLBACK_ORDER = ["hybrid", "graph", "hyde", "web", "ltm", "parametric"]

    def __init__(self) -> None:
        self.attempts: list[FallbackAttempt] = []

    @property
    def final_source(self) -> str:
        for attempt in reversed(self.attempts):
            if attempt.success:
                return attempt.source
        return "parametric"

    def record_attempt(self, source: str, *, success: bool, reason: str) -> None:
        self.attempts.append(FallbackAttempt(source=source, success=success, reason=reason))

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": [
                {"source": a.source, "success": a.success, "reason": a.reason}
                for a in self.attempts
            ],
            "final_source": self.final_source,
            "fallback_used": len(self.attempts) > 1,
        }
