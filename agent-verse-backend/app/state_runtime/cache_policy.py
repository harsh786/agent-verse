"""CachePolicyEngine — decides whether a step result should be cached."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class CacheDecision:
    should_cache: bool
    reason: str


class CachePolicyEngine:
    def decide(
        self,
        profile: "GoalRuntimeProfile",
        step_text: str,
        step_output: str,
        is_error: bool,
        is_nondeterministic: bool,
    ) -> CacheDecision:
        if not profile.memory_cache.use_semantic_cache:
            return CacheDecision(False, "semantic_cache disabled by profile")
        if is_error:
            return CacheDecision(False, "error outputs must not be cached")
        if is_nondeterministic:
            return CacheDecision(
                False, "non-deterministic result must not override fresh data"
            )
        return CacheDecision(True, "deterministic safe result — eligible for cache")
