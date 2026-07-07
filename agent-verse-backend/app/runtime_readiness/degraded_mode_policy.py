from __future__ import annotations
import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class DegradedModePolicy:
    def apply_degraded_rag(
        self,
        profile: "GoalRuntimeProfile",
        unavailable_deps: set[str],
    ) -> "GoalRuntimeProfile":
        rag = profile.rag_strategy
        if "embedder" in unavailable_deps:
            rag = dataclasses.replace(rag, embedding_model="lexical", strategy="naive_rag")
        if "kg_store" in unavailable_deps:
            rag = dataclasses.replace(rag, graph_strategy="none")
        if "web_search" in unavailable_deps:
            rag = dataclasses.replace(rag, web_fallback_enabled=False)
        return dataclasses.replace(profile, rag_strategy=rag)
