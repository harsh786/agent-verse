"""PromptBuilder — builds model-specific prompts from PromptContextBundle."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


_CHARS_PER_TOKEN = 4
# When the assembled prompt exceeds this fraction of the context window we
# run the PromptCompressor before returning to keep within model limits.
_AUTO_COMPRESS_THRESHOLD = 0.85


@dataclass
class PromptContextBundle:
    """All context sources assembled before prompt construction."""
    goal_context: str
    knowledge_chunks: list[dict[str, Any]]
    citations: list[Any]
    session_memory: list[dict[str, Any]]
    reflexion_lessons: list[str]
    execution_memory: list[dict[str, Any]] = field(default_factory=list)
    long_term_memory: list[dict[str, Any]] = field(default_factory=list)
    semantic_cache_hits: list[dict[str, Any]] = field(default_factory=list)
    graph_facts: list[dict[str, Any]] = field(default_factory=list)
    web_results: list[dict[str, Any]] = field(default_factory=list)
    degradation_notes: list[str] = field(default_factory=list)
    source_inventory: dict[str, Any] = field(default_factory=dict)


class PromptBuilder:
    def __init__(self, max_context_tokens: int = 6000) -> None:
        self._max_tokens = max_context_tokens

    def _estimate_tokens(self, text: str) -> int:
        """Cheap token count estimate: 1 token ≈ 4 chars."""
        return max(1, len(text) // _CHARS_PER_TOKEN)

    def _auto_compress(self, text: str, model_max_tokens: int | None = None) -> str:
        """Compress *text* if it exceeds _AUTO_COMPRESS_THRESHOLD of the context window."""
        limit = model_max_tokens or self._max_tokens
        threshold_chars = int(limit * _CHARS_PER_TOKEN * _AUTO_COMPRESS_THRESHOLD)
        if len(text) <= threshold_chars:
            return text
        try:
            from app.context.prompt_compressor import PromptCompressor
            compressor = PromptCompressor(target_tokens=int(limit * _AUTO_COMPRESS_THRESHOLD))
            compressed = compressor.compress(text)
            return compressed
        except Exception:
            # Fallback: hard truncate
            return text[:threshold_chars]

    def _truncate_chunks(self, chunks: list[dict[str, Any]], token_budget: int) -> str:
        parts = []
        used = 0
        for chunk in chunks:
            content = chunk.get("content", "")
            tokens = max(1, len(content) // _CHARS_PER_TOKEN)
            if used + tokens > token_budget:
                break
            citation_idx = chunk.get("_citation_index")
            ref = f" [{citation_idx}]" if citation_idx else ""
            parts.append(f"{content}{ref}")
            used += tokens
        return "\n\n".join(parts)

    def build_planner_context(self, bundle: PromptContextBundle) -> str:
        """Build context string for the planner LLM — includes all 9 sources."""
        sections = [f"Goal: {bundle.goal_context}"]
        token_budget = self._max_tokens

        if bundle.knowledge_chunks:
            chunk_text = self._truncate_chunks(bundle.knowledge_chunks, token_budget // 2)
            if chunk_text:
                sections.append(f"Knowledge:\n{chunk_text}")

        if bundle.session_memory:
            mem_text = "\n".join(
                str(m.get("content", m)) for m in bundle.session_memory[:3]
            )
            sections.append(f"Session context:\n{mem_text}")

        if bundle.execution_memory:
            plans = [str(m.get("plan", [])) for m in bundle.execution_memory[:2]]
            sections.append("Prior successful approaches:\n" + "\n".join(plans))

        if bundle.long_term_memory:
            prefs = "\n".join(
                str(m.get("content", m)) for m in bundle.long_term_memory[:3]
            )
            sections.append(f"Learned preferences:\n{prefs}")

        if bundle.semantic_cache_hits:
            cached = "\n".join(
                str(h.get("content", h)) for h in bundle.semantic_cache_hits[:2]
            )
            sections.append(f"[Cached context]\n{cached}")

        if bundle.graph_facts:
            facts = "\n".join(str(f.get("fact", f)) for f in bundle.graph_facts[:5])
            sections.append(f"Knowledge graph context:\n{facts}")

        if bundle.web_results:
            web_text = "\n".join(
                r.get("content", r.get("snippet", ""))[:200]
                for r in bundle.web_results[:3]
            )
            sections.append(f"Web context:\n{web_text}")

        if bundle.reflexion_lessons:
            lessons = "\n".join(f"- {l}" for l in bundle.reflexion_lessons[:3])
            sections.append(f"Past lessons:\n{lessons}")

        if bundle.citations:
            from app.context.citation_manager import CitationManager
            mgr = CitationManager()
            sections.append(mgr.format_citation_block(bundle.citations))

        if bundle.degradation_notes:
            notes = "; ".join(bundle.degradation_notes)
            sections.append(f"[Note: {notes}]")

        full = "\n\n".join(sections)
        # Auto-compress if prompt would overflow the context window
        return self._auto_compress(full)

    def build_executor_context(self, bundle: PromptContextBundle, step: str = "") -> str:
        """Build per-step context for the executor LLM."""
        sections = []
        if step:
            sections.append(f"Current step: {step}")

        if bundle.knowledge_chunks:
            chunk_text = self._truncate_chunks(bundle.knowledge_chunks, self._max_tokens // 2)
            if chunk_text:
                sections.append(f"Context:\n{chunk_text}")

        if bundle.citations:
            from app.context.citation_manager import CitationManager
            mgr = CitationManager()
            sections.append(mgr.format_citation_block(bundle.citations))

        if bundle.web_results:
            web_text = "\n".join(
                r.get("content", r.get("snippet", ""))[:200]
                for r in bundle.web_results[:3]
            )
            sections.append(f"Web results:\n{web_text}")

        return "\n\n".join(sections)

    def build_verifier_context(self, bundle: PromptContextBundle) -> str:
        """Build context for the verifier LLM — focus on citations and confidence."""
        sections = [f"Goal: {bundle.goal_context}"]
        if bundle.citations:
            from app.context.citation_manager import CitationManager
            mgr = CitationManager()
            sections.append(mgr.format_citation_block(bundle.citations))
        if bundle.degradation_notes:
            sections.append("Degradation notes: " + "; ".join(bundle.degradation_notes))
        return "\n\n".join(sections)
