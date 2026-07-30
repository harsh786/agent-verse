"""FallbackChain — tracks fallback attempts per doc-2 §4 exact order."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class FallbackAttempt:
    source: str
    success: bool
    reason: str


@dataclass(frozen=True, slots=True)
class FallbackDecision:
    source: str
    reason: str


class FallbackChain:
    # Doc-2 §4: exact fallback order
    FALLBACK_ORDER: ClassVar[list[str]] = [
        "hybrid",
        "graph",
        "hyde",
        "web",
        "ltm",
        "parametric",
    ]

    # Strategies that require external infrastructure
    REQUIRES_INFRA: ClassVar[dict[str, str]] = {
        "graph": "kg_store",
        "web": "web_search",
        "ltm": "ltm_store",
    }

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

    def next_available_strategy(
        self,
        current: str,
        available_infra: set[str] | None = None,
    ) -> str | None:
        """Return next available strategy, skipping those without required infra."""
        available_infra = available_infra or set()
        try:
            idx = self.FALLBACK_ORDER.index(current)
        except ValueError:
            idx = -1

        for s in self.FALLBACK_ORDER[idx + 1:]:
            required = self.REQUIRES_INFRA.get(s)
            if required is None or required in available_infra:
                return s
        return None

    def next_decision(
        self,
        current: str,
        *,
        reason: str,
        available_infra: set[str] | None = None,
    ) -> FallbackDecision | None:
        """Return and record the next explicit fallback source."""

        source = self.next_available_strategy(current, available_infra)
        if source is None:
            return None
        self.record_attempt(source, success=False, reason=reason)
        return FallbackDecision(source=source, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": [
                {"source": a.source, "success": a.success, "reason": a.reason}
                for a in self.attempts
            ],
            "final_source": self.final_source,
            "fallback_used": len(self.attempts) > 1,
        }

