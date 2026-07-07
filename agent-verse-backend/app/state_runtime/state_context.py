"""StateRuntimeContext — unified aggregator of all 8 state sources (spec §Layer 9)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.context.prompt_builder import PromptContextBundle
    from app.tenancy.context import TenantContext
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class StateRuntimeContext:
    session_memory: list[dict[str, Any]] = field(default_factory=list)
    execution_memory: list[dict[str, Any]] = field(default_factory=list)
    long_term_memory: list[dict[str, Any]] = field(default_factory=list)
    semantic_cache_hits: list[dict[str, Any]] = field(default_factory=list)
    knowledge_chunks: list[dict[str, Any]] = field(default_factory=list)
    graph_facts: list[dict[str, Any]] = field(default_factory=list)
    web_results: list[dict[str, Any]] = field(default_factory=list)
    reflexion_lessons: list[str] = field(default_factory=list)
    degradation_notes: list[str] = field(default_factory=list)

    def to_prompt_bundle(self, goal_context: str = "") -> "PromptContextBundle":
        from app.context.prompt_builder import PromptContextBundle

        return PromptContextBundle(
            goal_context=goal_context,
            knowledge_chunks=self.knowledge_chunks,
            citations=[],
            session_memory=self.session_memory,
            reflexion_lessons=self.reflexion_lessons,
            execution_memory=self.execution_memory,
            long_term_memory=self.long_term_memory,
            semantic_cache_hits=self.semantic_cache_hits,
            graph_facts=self.graph_facts,
            web_results=self.web_results,
            degradation_notes=self.degradation_notes,
        )


class StateContextBuilder:
    def __init__(
        self,
        *,
        execution_memory: Any = None,
        ltm_store: Any = None,
        reflexion_store: Any = None,
        knowledge_store: Any = None,
        kg_store: Any = None,
        semantic_cache: Any = None,
    ) -> None:
        self._exec = execution_memory
        self._ltm = ltm_store
        self._reflexion = reflexion_store
        self._kb = knowledge_store
        self._kg = kg_store
        self._cache = semantic_cache

    async def build(
        self,
        goal: str,
        *,
        tenant_ctx: "TenantContext",
        profile: "GoalRuntimeProfile",
        retrieval_chunks: list[dict[str, Any]] | None = None,
        web_results: list[dict[str, Any]] | None = None,
    ) -> StateRuntimeContext:
        mc = profile.memory_cache
        ctx = StateRuntimeContext()

        if mc.use_execution_memory and self._exec is not None:
            try:
                plans = self._exec.recall(goal_hint=goal, tenant_ctx=tenant_ctx, top_k=3)
                ctx.execution_memory = [
                    {"goal": p.get("goal", ""), "plan": p.get("plan", [])} for p in plans
                ]
            except Exception:
                ctx.degradation_notes.append("execution_memory recall failed")

        if mc.use_long_term_memory and self._ltm is not None:
            try:
                memories = self._ltm.recall(query=goal, tenant_ctx=tenant_ctx, top_k=5)
                from app.data_classification.classifier import DataClassifier

                classifier = DataClassifier()
                safe_memories = []
                for m in memories:
                    result = classifier.classify(m.content)
                    if result.safe_for_prompt:
                        safe_memories.append({
                            "content": m.content,
                            "memory_type": m.memory_type,
                            "confidence": m.confidence,
                        })
                ctx.long_term_memory = safe_memories
            except Exception:
                ctx.degradation_notes.append("long_term_memory recall failed")

        if mc.reflexion_enabled and self._reflexion is not None:
            try:
                lessons = self._reflexion.recall(tenant_id=tenant_ctx.tenant_id, limit=3)
                ctx.reflexion_lessons = [l["lesson"] for l in lessons]
            except Exception:
                ctx.degradation_notes.append("reflexion_store recall failed")

        if retrieval_chunks:
            ctx.knowledge_chunks = retrieval_chunks
        if web_results:
            ctx.web_results = web_results

        return ctx
