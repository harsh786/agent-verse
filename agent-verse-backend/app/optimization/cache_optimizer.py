"""CacheOptimizer — identifies caching opportunities to reduce redundant LLM calls."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CacheDecisionResult:
    should_cache: bool
    reason: str


@dataclass
class CacheOpportunity:
    cache_type: str   # "semantic" | "exact" | "plan"
    estimated_hit_rate: float
    description: str


class CacheOptimizer:
    def __init__(self) -> None:
        self._call_patterns: dict[str, int] = {}

    # ── Legacy interface (existing tests) ─────────────────────────────────────

    def should_cache(
        self,
        query: str,
        result_count: int,
        latency_ms: float,
        is_realtime: bool,
        has_time_sensitive_content: bool,
    ) -> CacheDecisionResult:
        if is_realtime or has_time_sensitive_content:
            return CacheDecisionResult(False, "realtime/time-sensitive — do not cache")
        if result_count == 0:
            return CacheDecisionResult(False, "empty result — do not cache")
        return CacheDecisionResult(
            True, f"stable query latency={latency_ms:.0f}ms — eligible"
        )

    # ── New interface (pattern-based opportunity detection) ───────────────────

    def record_call(self, call_signature: str) -> None:
        self._call_patterns[call_signature] = self._call_patterns.get(call_signature, 0) + 1

    def get_opportunities(self) -> list[CacheOpportunity]:
        opportunities: list[CacheOpportunity] = []
        repeat_calls = sum(1 for v in self._call_patterns.values() if v > 1)
        total = len(self._call_patterns)
        if total == 0:
            return []
        hit_rate = repeat_calls / total
        if hit_rate > 0.3:
            opportunities.append(CacheOpportunity(
                "semantic", hit_rate,
                f"{repeat_calls}/{total} calls are repeated — semantic cache recommended",
            ))
        if hit_rate > 0.6:
            opportunities.append(CacheOpportunity(
                "exact", hit_rate * 0.5,
                "High repeat rate — exact-match cache for identical prompts",
            ))
        return opportunities
