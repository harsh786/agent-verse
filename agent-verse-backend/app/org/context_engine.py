"""Context Engine — builds optimal LLM context for every agent call.

Retrieves from 10 priority sources, ranks by relevance + priority,
semantic-deduplicates, compresses to fit the token budget, and
returns an OptimizedContext with full provenance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar

import structlog
from opentelemetry import trace
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.org.models import OrgDecision, OrgMission, OrgRole

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

_TOKENS_PER_CHAR: float = 0.25  # ~4 chars per token
_DEDUP_THRESHOLD: float = 0.92
_MAX_ITEM_CHARS: int = 3_000  # hard cap per item before compression


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * _TOKENS_PER_CHAR))


def _term_relevance(query: str, text: str) -> float:
    if not query or not text:
        return 0.0
    query_terms = set(query.lower().split())
    text_lower = text.lower()
    matches = sum(1 for t in query_terms if t in text_lower)
    return min(1.0, matches / max(1, len(query_terms)))


def _ngram_fingerprint(text: str) -> set[str]:
    words = text.lower().split()
    return (
        {f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)}
        if len(words) > 1
        else set(words)
    )


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ContextStrategy:
    max_tokens: int
    compression: str  # "aggressive" | "moderate" | "light" | "none" | "semantic_only"


@dataclass
class ContextItem:
    source: str
    content: str
    relevance: float  # 0–1
    tokens_estimate: int
    priority: int  # lower = higher priority (index in PRIORITY_SOURCES)


@dataclass
class OptimizedContext:
    items: list[ContextItem]
    total_tokens: int
    provenance: list[str]  # sorted unique source names
    compression_ratio: float
    strategy_used: str


@dataclass
class ContextBuildRequest:
    agent_id: str | None
    task_description: str
    mission_id: str | None
    dept_id: str | None
    org_id: str
    tenant_id: str
    max_tokens: int = 6_000
    strategy: str = "standard"  # quick|standard|deep|research|code


# ─────────────────────────────────────────────────────────────────────────────
#  Engine
# ─────────────────────────────────────────────────────────────────────────────


class ContextEngine:
    """
    Builds optimal context for every LLM call.

    Sources (in priority order):
      0  task_instructions     — the task itself (always included first)
      1  mission_requirements  — current mission goal/constraints
      2  recent_decisions      — last N org decisions relevant to task
      3  working_memory        — agent's current task state
      4  dept_knowledge        — department-scoped knowledge retrieval
      5  relevant_knowledge    — org knowledge base RAG results
      6  similar_past_work     — past completed similar tasks
      7  org_memory            — org-wide lessons / long-term memory
      8  role_guidelines       — role-specific behaviour guidelines
      9  org_policies          — always compressed, always last
    """

    CONTEXT_STRATEGIES: ClassVar[dict[str, ContextStrategy]] = {
        "quick": ContextStrategy(max_tokens=2_000, compression="aggressive"),
        "standard": ContextStrategy(max_tokens=6_000, compression="moderate"),
        "deep": ContextStrategy(max_tokens=12_000, compression="light"),
        "research": ContextStrategy(max_tokens=20_000, compression="none"),
        "code": ContextStrategy(max_tokens=8_000, compression="semantic_only"),
    }

    PRIORITY_SOURCES: ClassVar[list[str]] = [
        "task_instructions",
        "mission_requirements",
        "recent_decisions",
        "working_memory",
        "dept_knowledge",
        "relevant_knowledge",
        "similar_past_work",
        "org_memory",
        "role_guidelines",
        "org_policies",
    ]

    def __init__(
        self,
        session: AsyncSession,
        knowledge_store: Any | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self._s = session
        self._ks = knowledge_store
        self._ms = memory_store

    async def build_context(self, req: ContextBuildRequest) -> OptimizedContext:
        with _tracer.start_as_current_span("context_engine.build_context") as span:
            span.set_attribute("org_id", req.org_id)
            span.set_attribute("strategy", req.strategy)
            span.set_attribute("mission_id", req.mission_id or "")
            _log.info("context_engine.build.start", org_id=req.org_id, strategy=req.strategy)

            strat = self.CONTEXT_STRATEGIES.get(req.strategy, self.CONTEXT_STRATEGIES["standard"])
            effective_max = min(req.max_tokens, strat.max_tokens)

            # Task instruction is always item 0
            all_items: list[ContextItem] = [
                ContextItem(
                    source="task_instructions",
                    content=req.task_description,
                    relevance=1.0,
                    tokens_estimate=estimate_tokens(req.task_description),
                    priority=0,
                )
            ]

            gathered = await self._gather_sources(req)
            all_items.extend(gathered)

            tokens_before = sum(i.tokens_estimate for i in all_items)
            all_items = await self._semantic_dedup(all_items)
            selected = await self._rank_and_select(all_items, effective_max)
            selected = await self._compress(selected, strat.compression)
            tokens_after = sum(i.tokens_estimate for i in selected)

            result = OptimizedContext(
                items=selected,
                total_tokens=tokens_after,
                provenance=sorted({i.source for i in selected}),
                compression_ratio=tokens_before / max(1, tokens_after),
                strategy_used=req.strategy,
            )
            span.set_attribute("items_count", len(selected))
            span.set_attribute("total_tokens", tokens_after)
            _log.info(
                "context_engine.build.done",
                org_id=req.org_id,
                items=len(selected),
                tokens=tokens_after,
            )
            return result

    # ── Source retrieval ───────────────────────────────────────────────────

    async def _gather_sources(self, req: ContextBuildRequest) -> list[ContextItem]:
        tasks = [
            self._mission_context(req.mission_id, req.tenant_id, req.task_description),
            self._recent_decisions(req.org_id, req.tenant_id, req.task_description),
            self._working_memory(req.agent_id, req.tenant_id),
            self._dept_memory(req.dept_id, req.task_description),
            self._knowledge_store(req.org_id, req.tenant_id, req.task_description),
            self._long_term_memory(req.agent_id, req.tenant_id, req.task_description),
            self._role_guidelines(req.org_id, req.tenant_id),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[ContextItem] = []
        for r in results:
            if isinstance(r, BaseException):
                _log.warning("context_engine.source_error", error=str(r))
            elif isinstance(r, list):
                items.extend(r)
        return items

    async def _mission_context(
        self,
        mission_id: str | None,
        tenant_id: str,
        query: str,
    ) -> list[ContextItem]:
        if not mission_id:
            return []
        try:
            res = await self._s.execute(
                select(OrgMission).where(
                    and_(
                        OrgMission.tenant_id == tenant_id,
                        OrgMission.id == mission_id,
                    )
                )
            )
            m = res.scalar_one_or_none()
            if m is None:
                return []
            parts = []
            for attr, label in (
                ("title", "Mission"),
                ("objective", "Objective"),
                ("description", "Description"),
                ("requirements", "Requirements"),
            ):
                val = getattr(m, attr, None)
                if val:
                    parts.append(f"{label}: {val}")
            if not parts:
                return []
            content = "\n".join(parts)
            return [
                ContextItem(
                    source="mission_requirements",
                    content=content,
                    relevance=0.95,
                    tokens_estimate=estimate_tokens(content),
                    priority=self.PRIORITY_SOURCES.index("mission_requirements"),
                )
            ]
        except Exception as exc:
            _log.warning("context_engine._mission_context.failed", error=str(exc))
            return []

    async def _recent_decisions(
        self,
        org_id: str,
        tenant_id: str,
        query: str,
    ) -> list[ContextItem]:
        try:
            res = await self._s.execute(
                select(OrgDecision)
                .where(
                    and_(
                        OrgDecision.tenant_id == tenant_id,
                        OrgDecision.org_id == org_id,
                    )
                )
                .order_by(OrgDecision.created_at.desc())
                .limit(8)
            )
            prio = self.PRIORITY_SOURCES.index("recent_decisions")
            items: list[ContextItem] = []
            for d in res.scalars().all():
                parts = []
                for attr, label in (
                    ("title", "Decision"),
                    ("rationale", "Rationale"),
                    ("outcome", "Outcome"),
                    ("description", "Description"),
                ):
                    val = getattr(d, attr, None)
                    if val:
                        parts.append(f"{label}: {val}")
                if not parts:
                    continue
                content = "\n".join(parts)
                items.append(
                    ContextItem(
                        source="recent_decisions",
                        content=content,
                        relevance=_term_relevance(query, content),
                        tokens_estimate=estimate_tokens(content),
                        priority=prio,
                    )
                )
            return items
        except Exception as exc:
            _log.warning("context_engine._recent_decisions.failed", error=str(exc))
            return []

    async def _working_memory(
        self,
        agent_id: str | None,
        tenant_id: str,
    ) -> list[ContextItem]:
        if not agent_id or self._ms is None:
            return []
        try:
            entries = await self._ms.get_working_memory(
                agent_id=agent_id,
                tenant_id=tenant_id,
            )
            prio = self.PRIORITY_SOURCES.index("working_memory")
            return [
                ContextItem(
                    source="working_memory",
                    content=str(e.get("content", "")),
                    relevance=float(e.get("relevance", 0.7)),
                    tokens_estimate=estimate_tokens(str(e.get("content", ""))),
                    priority=prio,
                )
                for e in (entries or [])
                if e.get("content")
            ]
        except Exception as exc:
            _log.warning("context_engine._working_memory.failed", error=str(exc))
            return []

    async def _dept_memory(
        self,
        dept_id: str | None,
        query: str,
    ) -> list[ContextItem]:
        if not dept_id or self._ks is None:
            return []
        try:
            results = await self._ks.search(
                query=query,
                filters={"dept_id": dept_id},
                limit=5,
            )
            prio = self.PRIORITY_SOURCES.index("dept_knowledge")
            return [
                ContextItem(
                    source="dept_knowledge",
                    content=str(r.get("content", "")),
                    relevance=float(r.get("score", 0.5)),
                    tokens_estimate=estimate_tokens(str(r.get("content", ""))),
                    priority=prio,
                )
                for r in (results or [])
                if r.get("content")
            ]
        except Exception as exc:
            _log.warning("context_engine._dept_memory.failed", error=str(exc))
            return []

    async def _knowledge_store(
        self,
        org_id: str,
        tenant_id: str,
        query: str,
    ) -> list[ContextItem]:
        if self._ks is None:
            return []
        try:
            results = await self._ks.search(
                query=query,
                filters={"org_id": org_id},
                limit=8,
            )
            prio = self.PRIORITY_SOURCES.index("relevant_knowledge")
            return [
                ContextItem(
                    source="relevant_knowledge",
                    content=str(r.get("content", "")),
                    relevance=float(r.get("score", 0.5)),
                    tokens_estimate=estimate_tokens(str(r.get("content", ""))),
                    priority=prio,
                )
                for r in (results or [])
                if r.get("content")
            ]
        except Exception as exc:
            _log.warning("context_engine._knowledge_store.failed", error=str(exc))
            return []

    async def _long_term_memory(
        self,
        agent_id: str | None,
        tenant_id: str,
        query: str,
    ) -> list[ContextItem]:
        if not agent_id or self._ms is None:
            return []
        try:
            entries = await self._ms.search(
                agent_id=agent_id,
                tenant_id=tenant_id,
                query=query,
                limit=5,
            )
            prio = self.PRIORITY_SOURCES.index("org_memory")
            return [
                ContextItem(
                    source="org_memory",
                    content=str(e.get("content", "")),
                    relevance=float(e.get("score", 0.5)),
                    tokens_estimate=estimate_tokens(str(e.get("content", ""))),
                    priority=prio,
                )
                for e in (entries or [])
                if e.get("content")
            ]
        except Exception as exc:
            _log.warning("context_engine._long_term_memory.failed", error=str(exc))
            return []

    async def _role_guidelines(
        self,
        org_id: str,
        tenant_id: str,
    ) -> list[ContextItem]:
        try:
            res = await self._s.execute(
                select(OrgRole)
                .where(
                    and_(
                        OrgRole.tenant_id == tenant_id,
                        OrgRole.org_id == org_id,
                    )
                )
                .limit(3)
            )
            prio = self.PRIORITY_SOURCES.index("role_guidelines")
            items: list[ContextItem] = []
            for role in res.scalars().all():
                parts = []
                for attr, label in (
                    ("name", "Role"),
                    ("responsibilities", "Responsibilities"),
                ):
                    val = getattr(role, attr, None)
                    if val:
                        parts.append(f"{label}: {val}")
                if not parts:
                    continue
                content = "\n".join(parts)
                items.append(
                    ContextItem(
                        source="role_guidelines",
                        content=content,
                        relevance=0.4,
                        tokens_estimate=estimate_tokens(content),
                        priority=prio,
                    )
                )
            return items
        except Exception as exc:
            _log.warning("context_engine._role_guidelines.failed", error=str(exc))
            return []

    # ── Ranking + dedup + compression ─────────────────────────────────────

    async def _rank_and_select(
        self,
        items: list[ContextItem],
        max_tokens: int,
    ) -> list[ContextItem]:
        """Priority-first then relevance, cut at token budget."""
        sorted_items = sorted(items, key=lambda i: (i.priority, -i.relevance))
        selected: list[ContextItem] = []
        used = 0
        for item in sorted_items:
            if used + item.tokens_estimate <= max_tokens:
                selected.append(item)
                used += item.tokens_estimate
        return selected

    async def _semantic_dedup(
        self,
        items: list[ContextItem],
        threshold: float = _DEDUP_THRESHOLD,
    ) -> list[ContextItem]:
        """Remove near-duplicate items by bigram Jaccard similarity."""
        kept: list[ContextItem] = []
        fps: list[set[str]] = []
        for item in items:
            fp = _ngram_fingerprint(item.content[:500])
            if all(_jaccard(fp, existing) < threshold for existing in fps):
                kept.append(item)
                fps.append(fp)
        return kept

    async def _compress(
        self,
        items: list[ContextItem],
        strategy: str,
    ) -> list[ContextItem]:
        """Truncate long items based on compression strategy."""
        limits = {
            "aggressive": 200,
            "moderate": 500,
            "light": 1_000,
            "semantic_only": 800,
            "none": _MAX_ITEM_CHARS,
        }
        char_limit = limits.get(strategy, 500)
        result: list[ContextItem] = []
        for item in items:
            if len(item.content) <= char_limit:
                result.append(item)
            else:
                truncated = item.content[:char_limit] + "…"
                result.append(
                    ContextItem(
                        source=item.source,
                        content=truncated,
                        relevance=item.relevance,
                        tokens_estimate=estimate_tokens(truncated),
                        priority=item.priority,
                    )
                )
        return result


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton factory
# ─────────────────────────────────────────────────────────────────────────────


def get_context_engine(
    session: AsyncSession,
    knowledge_store: Any | None = None,
    memory_store: Any | None = None,
) -> ContextEngine:
    """Return a ContextEngine bound to the given session."""
    return ContextEngine(
        session=session, knowledge_store=knowledge_store, memory_store=memory_store
    )
