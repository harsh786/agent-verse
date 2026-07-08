"""Speculative RAG — parallel candidate generation + retrieval-based verification.

Algorithm:
  1. Generate N candidate answers in parallel (fast, low-temperature)
  2. For each candidate: retrieve supporting context
  3. Score each candidate against retrieved context
  4. Return highest-scoring supported candidate

Inspired by speculative decoding applied to RAG:
  fast speculation → slow verification → confident answer
"""
from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

_CANDIDATE_SYSTEM = """Generate a concise, direct answer to this question.
Be specific and factual."""

_VERIFY_SYSTEM = """Given a question, a candidate answer, and supporting context,
score how well the context supports the answer.
Respond with JSON: {"score": <0.0-1.0>, "supported": <true/false>}
Score 0.9+ if context directly confirms the answer. Score below 0.5 if context contradicts or doesn't support."""


@dataclass
class Candidate:
    text: str
    score: float = 0.0
    supported: bool = False
    context_used: str = ""


class SpeculativeRAGPattern(RAGPattern):
    """Speculative RAG: parallel candidates + retrieval verification."""

    def __init__(
        self,
        n_candidates: int = 3,
        min_support_score: float = 0.6,
    ) -> None:
        self._n = n_candidates
        self._min_score = min_support_score
        self._circuit_breakers: dict[str, Any] = {}

    @property
    def pattern_id(self) -> str:
        return "speculative_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            f"Speculative RAG: generate {self._n} candidate answers in parallel, "
            "retrieve supporting context for each, verify and score — return "
            "highest-supported answer. Fast speculation + slow verification."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings
            if not get_settings().enable_speculative_rag:
                return False
        except Exception:
            pass
        return True

    async def execute(
        self,
        *,
        query: str,
        provider: Any,
        retrieve_fn: Callable[[str], Awaitable[str]] | None = None,
        max_tokens: int = 500,
        **kwargs: Any,
    ) -> str:
        """Generate N candidates, verify each, return best-supported."""
        try:
            from app.observability.logging import get_logger
            get_logger(__name__).info("speculative_rag_started", query=query[:60])
        except Exception:
            pass

        from app.providers.base import CompletionRequest, Message

        # Circuit breaker setup
        try:
            from app.reliability.circuit_breaker import CircuitBreaker
            _cb_key = f"pattern_{self.pattern_id}"
            if _cb_key not in self._circuit_breakers:
                self._circuit_breakers[_cb_key] = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
            cb: Any = self._circuit_breakers[_cb_key]
        except ImportError:
            cb = None

        # Step 1: Generate candidates sequentially to ensure deterministic provider ordering
        candidates: list[Candidate] = []
        for _ in range(self._n):
            try:
                if cb is not None and not cb.can_call():
                    break
                resp = await provider.complete(CompletionRequest(
                    messages=[
                        Message(role="system", content=_CANDIDATE_SYSTEM),
                        Message(role="user", content=query),
                    ],
                    model="",
                    max_tokens=max_tokens,
                    temperature=0.7,  # diversity
                ))
                if cb is not None:
                    cb.record_success()
                text = (resp.content or "").strip()
                if text:
                    candidates.append(Candidate(text=text))
            except Exception:
                if cb is not None:
                    cb.record_failure()

        if not candidates:
            try:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("speculative_rag_failed", error="no candidates generated")
            except Exception:
                pass
            return ""

        # Step 2: Retrieve + verify each candidate sequentially
        verified: list[Candidate] = []
        for candidate in candidates:
            context = ""
            if retrieve_fn is not None:
                try:
                    context = await retrieve_fn(candidate.text[:200]) or ""
                    candidate.context_used = context[:500]
                except Exception:
                    pass

            if not context:
                candidate.score = 0.3
                verified.append(candidate)
                continue

            try:
                if cb is not None and not cb.can_call():
                    candidate.score = 0.3
                    verified.append(candidate)
                    continue
                resp = await provider.complete(CompletionRequest(
                    messages=[
                        Message(role="system", content=_VERIFY_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Question: {query[:200]}\n"
                                f"Candidate: {candidate.text[:400]}\n"
                                f"Context: {context[:800]}"
                            ),
                        ),
                    ],
                    model="",
                    max_tokens=100,
                    temperature=0.0,
                    response_schema={
                        "type": "object",
                        "properties": {
                            "score": {"type": "number"},
                            "supported": {"type": "boolean"},
                        },
                    },
                ))
                if cb is not None:
                    cb.record_success()
                raw = (resp.content or "").strip()
                try:
                    d = json.loads(raw)
                    candidate.score = max(0.0, min(1.0, float(d.get("score", 0.3))))
                    candidate.supported = bool(d.get("supported", candidate.score >= self._min_score))
                except Exception:
                    candidate.score = 0.3
            except Exception:
                if cb is not None:
                    cb.record_failure()
                candidate.score = 0.3
            verified.append(candidate)

        # Step 3: Return best supported candidate
        supported = [c for c in verified if c.supported]
        if supported:
            best = max(supported, key=lambda c: c.score)
        else:
            best = max(verified, key=lambda c: c.score)

        try:
            from app.observability.logging import get_logger
            get_logger(__name__).info("speculative_rag_completed", result_len=len(best.text))
        except Exception:
            pass
        return best.text
