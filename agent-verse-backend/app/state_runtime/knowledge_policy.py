"""KnowledgePolicyEngine — decides which knowledge sources to use per profile."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import KnowledgeRuntimeProfile


@dataclass
class KnowledgeDecision:
    use_kb: bool
    use_graph: bool
    use_web_fallback: bool
    use_memory: bool


class KnowledgePolicyEngine:
    def decide(
        self,
        profile: "KnowledgeRuntimeProfile",
        query_type: str = "factual",
    ) -> KnowledgeDecision:
        use_kb = profile.kb_state not in ("empty",)
        use_graph = (
            profile.graph_state not in ("empty",)
            and profile.graph_strategy != "none"
            and query_type in ("relationship", "impact", "dependency", "causal")
        )
        use_web = profile.web_fallback_required or profile.kb_state == "empty"
        return KnowledgeDecision(
            use_kb=use_kb,
            use_graph=use_graph,
            use_web_fallback=use_web,
            use_memory=True,
        )
