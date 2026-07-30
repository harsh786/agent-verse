"""Self-RAG — Retrieve on demand with critique tokens.

Asai et al. 2023: 'Self-RAG: Learning to Retrieve, Generate and Critique through Self-Reflection'

Critique tokens (simulated via LLM prompts):
  - [Retrieve]: should I retrieve for this query?
  - [ISREL]: is the retrieved doc relevant?
  - [ISSUP]: is my response supported by the doc?
  - [ISUSE]: is the overall response useful?

Algorithm:
  1. Decide if retrieval is needed (ShouldRetrieve)
  2. If yes: retrieve, check ISREL
  3. Generate response (with or without context)
  4. Check ISSUP + ISUSE
  5. Return response with critique metadata
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

_SHOULD_RETRIEVE_SYSTEM = """Decide if the following query requires external retrieval.
Respond with JSON: {"should_retrieve": <true/false>, "reason": "<brief reason>"}

Retrieve=true for: factual questions, current events, specific data, domain knowledge
Retrieve=false for: math, simple reasoning, greetings, general knowledge you're confident about"""

_CRITIQUE_SYSTEM = """Evaluate this response against the retrieved context.
Respond with JSON:
{
  "is_relevant": <true/false>,      // is context relevant to query?
  "is_supported": <true/false>,     // is response supported by context?
  "is_useful": <true/false>,        // is response useful for the query?
  "confidence": <0.0-1.0>
}"""

_GENERATE_WITH_CONTEXT = """Using the provided context, answer the question accurately.
If the context doesn't help, use your best knowledge."""


@dataclass
class SelfRAGResult:
    answer: str
    retrieved: bool = False
    context_used: str = ""
    is_relevant: bool = True
    is_supported: bool = True
    is_useful: bool = True
    confidence: float = 0.7


class SelfRAGPattern(RAGPattern):
    """Self-RAG: adaptive retrieval with critique tokens."""

    def __init__(self, confidence_threshold: float = 0.5) -> None:
        self._threshold = confidence_threshold
        self._circuit_breakers: dict[str, Any] = {}

    @property
    def pattern_id(self) -> str:
        return "self_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Self-RAG: decides whether to retrieve per query, "
            "evaluates retrieved context (ISREL), response support (ISSUP), "
            "and usefulness (ISUSE). Returns critique-informed answer."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings
            if not get_settings().enable_self_rag:
                return False
        except Exception:
            pass
        return True

    async def _complete_with_breaker(
        self,
        *,
        provider: Any,
        request: Any,
        breaker: Any,
        strict: bool,
    ) -> Any | None:
        """Run one provider call with side-effect-free circuit blocking."""

        if breaker is not None and not breaker.can_call():
            if strict:
                raise RuntimeError("Self-RAG provider circuit is open")
            return None
        try:
            response = await provider.complete(request)
        except Exception as exc:
            if breaker is not None:
                breaker.record_failure()
            if strict:
                raise RuntimeError("Self-RAG provider call failed") from exc
            return None
        if breaker is not None:
            breaker.record_success()
        return response

    async def execute(
        self,
        *,
        query: str,
        provider: Any,
        retrieve_fn: Callable[[str], Awaitable[str]] | None = None,
        max_tokens: int = 800,
        model: str = "",
        strict: bool = False,
        **kwargs: Any,
    ) -> str:
        """Execute Self-RAG with critique tokens. Returns answer string."""
        try:
            from app.observability.logging import get_logger
            get_logger(__name__).info("self_rag_started", query=query[:60])
        except Exception:
            pass
        result = await self.execute_with_critique(
            query=query, provider=provider,
            retrieve_fn=retrieve_fn, max_tokens=max_tokens, model=model, strict=strict,
        )
        try:
            from app.observability.logging import get_logger
            get_logger(__name__).info("self_rag_completed", result_len=len(result.answer))
        except Exception:
            pass
        return result.answer

    async def execute_with_critique(
        self,
        *,
        query: str,
        provider: Any,
        retrieve_fn: Callable[[str], Awaitable[str]] | None = None,
        max_tokens: int = 800,
        model: str = "",
        strict: bool = False,
    ) -> SelfRAGResult:
        """Execute Self-RAG. Returns SelfRAGResult with full critique metadata."""
        from app.providers.base import CompletionRequest, Message

        # Circuit breaker setup
        try:
            from app.reliability.circuit_breaker import CircuitBreaker
            _cb_key = f"pattern_{self.pattern_id}"
            if _cb_key not in self._circuit_breakers:
                self._circuit_breakers[_cb_key] = CircuitBreaker(
                    failure_threshold=5,
                    cooldown_seconds=30,
                )
            cb: Any = self._circuit_breakers[_cb_key]
        except ImportError:
            cb = None

        # Step 1: Decide if retrieval needed
        should_retrieve = await self._should_retrieve(
            query,
            provider,
            model,
            strict,
            cb,
        )

        context = ""
        if should_retrieve and retrieve_fn is not None:
            try:
                context = await retrieve_fn(query) or ""
            except Exception:
                if strict:
                    raise
                context = ""

        # Step 2: Generate response
        if context:
            messages = [
                Message(role="system", content=_GENERATE_WITH_CONTEXT),
                Message(
                    role="user",
                    content=f"Context:\n{context[:1500]}\n\nQuestion: {query}",
                ),
            ]
        else:
            messages = [Message(role="user", content=query)]

        resp = await self._complete_with_breaker(
            provider=provider,
            request=CompletionRequest(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=0.0,
            ),
            breaker=cb,
            strict=strict,
        )
        if resp is None:
            return SelfRAGResult(answer="", retrieved=bool(context))
        answer = (resp.content or "").strip()

        # Step 3: Critique (only if we retrieved)
        is_relevant = is_supported = is_useful = True
        confidence = 0.7
        if context:
            critique = await self._critique(
                query,
                answer,
                context,
                provider,
                model,
                strict,
                cb,
            )
            is_relevant = critique.get("is_relevant", True)
            is_supported = critique.get("is_supported", True)
            is_useful = critique.get("is_useful", True)
            confidence = float(critique.get("confidence", 0.7))

        return SelfRAGResult(
            answer=answer,
            retrieved=bool(context),
            context_used=context[:500] if context else "",
            is_relevant=is_relevant,
            is_supported=is_supported,
            is_useful=is_useful,
            confidence=confidence,
        )

    async def _should_retrieve(
        self,
        query: str,
        provider: Any,
        model: str = "",
        strict: bool = False,
        breaker: Any = None,
    ) -> bool:
        from app.providers.base import CompletionRequest, Message
        resp = await self._complete_with_breaker(
            provider=provider,
            request=CompletionRequest(
                messages=[
                    Message(role="system", content=_SHOULD_RETRIEVE_SYSTEM),
                    Message(role="user", content=f"Query: {query[:300]}"),
                ],
                model=model,
                max_tokens=100,
                temperature=0.0,
                response_schema={
                    "type": "object",
                    "properties": {
                        "should_retrieve": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                },
            ),
            breaker=breaker,
            strict=strict,
        )
        if resp is None:
            return True
        raw = (resp.content or "").strip()
        try:
            d = json.loads(raw)
            return bool(d.get("should_retrieve", True))
        except Exception:
            return True  # default: retrieve

    async def _critique(
        self,
        query: str,
        answer: str,
        context: str,
        provider: Any,
        model: str = "",
        strict: bool = False,
        breaker: Any = None,
    ) -> dict[str, Any]:
        from app.providers.base import CompletionRequest, Message
        resp = await self._complete_with_breaker(
            provider=provider,
            request=CompletionRequest(
                messages=[
                    Message(role="system", content=_CRITIQUE_SYSTEM),
                    Message(
                        role="user",
                        content=(
                            f"Query: {query[:200]}\n"
                            f"Context: {context[:800]}\n"
                            f"Response: {answer[:800]}"
                        ),
                    ),
                ],
                model=model,
                max_tokens=150,
                temperature=0.0,
                response_schema={
                    "type": "object",
                    "properties": {
                        "is_relevant": {"type": "boolean"},
                        "is_supported": {"type": "boolean"},
                        "is_useful": {"type": "boolean"},
                        "confidence": {"type": "number"},
                    },
                },
            ),
            breaker=breaker,
            strict=strict,
        )
        if resp is None:
            return {"is_relevant": True, "is_supported": True, "is_useful": True, "confidence": 0.6}
        raw = (resp.content or "").strip()
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {"is_relevant": True, "is_supported": True, "is_useful": True, "confidence": 0.6}
