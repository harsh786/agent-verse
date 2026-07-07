from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CacheDecisionResult:
    should_cache: bool
    reason: str


class CacheOptimizer:
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
