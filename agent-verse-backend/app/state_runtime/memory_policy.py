"""MemoryPolicyEngine — decides which memory sources to activate per profile."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class MemoryDecision:
    use_session_memory: bool
    use_execution_memory: bool
    use_long_term_memory: bool
    use_knowledge_graph: bool
    reflexion_enabled: bool


class MemoryPolicyEngine:
    def decide(self, profile: "GoalRuntimeProfile") -> MemoryDecision:
        mc = profile.memory_cache
        return MemoryDecision(
            use_session_memory=mc.use_session_memory,
            use_execution_memory=mc.use_execution_memory,
            use_long_term_memory=mc.use_long_term_memory,
            use_knowledge_graph=mc.use_knowledge_graph,
            reflexion_enabled=mc.reflexion_enabled,
        )
