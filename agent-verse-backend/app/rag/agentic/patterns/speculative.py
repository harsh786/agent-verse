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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.contracts import (
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.contracts import (
    SpeculativeRAGRuntimeAdapter as SpeculativeRAGRuntimeContract,
)
from app.rag.engine import RetrievalStrategyExecutionError, first_task_group_exception

_CANDIDATE_SYSTEM = """Answer the question using only the evidence provided.
Be concise, direct and specific. If the evidence does not contain the answer, say so."""

_VERIFY_SYSTEM = (
    "Given a question, a candidate answer, and supporting context,\n"
    "score how well the context supports the answer.\n"
    'Respond with JSON: {"score": <0.0-1.0>, "supported": <true/false>, '
    '"verified_claims": [<sentences copied word for word from the candidate that the '
    'context supports>]}\n'
    "Score 0.9+ if context directly confirms the answer. Score below 0.5 if context "
    "contradicts or doesn't support. Copy each verified claim exactly as it appears in "
    "the candidate; leave out any sentence the context does not support."
)


@dataclass
class Candidate:
    text: str
    score: float = 0.0
    supported: bool = False
    context_used: str = ""
    verified_claims: list[str] | None = None


class SpeculativeRAGRuntimeAdapter(SpeculativeRAGRuntimeContract):
    """Canonical speculative adapter with concurrent drafting and retrieval."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.SPECULATIVE

    def __init__(self, candidate_count: int = 3) -> None:
        if candidate_count < 1:
            raise ValueError("candidate_count must be positive")
        self._candidate_count = candidate_count

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.providers.base import CompletionRequest, Message
        from app.rag.gateway import (
            _canonical_result,
            _embed_text,
            _extend_trace,
            _search_persisted,
        )

        if context is None or context.llm is None or context.llm.provider is None:
            raise RetrievalStrategyExecutionError(self.strategy.value, "resolved LLM is required")

        provider = context.llm.provider
        model = context.llm.model
        evidence: list[dict[str, Any]] = []

        async def retrieve() -> list[Any]:
            embedding = await _embed_text(context, request.query, self.strategy)
            return await _search_persisted(
                context,
                request,
                query=request.query,
                embedding=embedding,
                retrieval_mode="hybrid",
                evidence=evidence,
            )

        # Retrieve FIRST, then draft each candidate from a different subset of
        # the retrieved documents (Speculative RAG: specialist drafts over
        # evidence subsets, verified against the whole set). Drafts used to be
        # written with no context at all — from the model's general knowledge —
        # so no private-knowledge question could ever be "supported" and the
        # strategy failed for exactly the questions a knowledge base exists for.
        results = await retrieve()
        if not results:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "no persisted evidence to draft from"
            )
        subset_count = max(1, min(self._candidate_count, len(results)))
        subsets = [results[i::subset_count] for i in range(subset_count)]

        async def generate_draft(subset: list[Any]) -> Candidate:
            evidence_text = "\n\n".join(item.content for item in subset)
            response = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_CANDIDATE_SYSTEM),
                        Message(
                            role="user",
                            content=f"Question: {request.query}\nEvidence:\n{evidence_text}",
                        ),
                    ],
                    model=model,
                    max_tokens=500,
                    temperature=0.7,
                )
            )
            text = response.content.strip()
            if not text:
                raise RetrievalStrategyExecutionError(
                    self.strategy.value, "draft generation returned empty content"
                )
            return Candidate(text=text, context_used=evidence_text)

        try:
            async with asyncio.TaskGroup() as task_group:
                draft_tasks = [
                    task_group.create_task(generate_draft(subset)) for subset in subsets
                ]
        except BaseExceptionGroup as exc:
            raise first_task_group_exception(exc) from None

        candidates = [task.result() for task in draft_tasks]
        context_text = "\n\n".join(result.content for result in results)

        async def verify(candidate: Candidate) -> Candidate:
            response = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_VERIFY_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Question: {request.query}\n"
                                f"Candidate: {candidate.text}\n"
                                f"Context: {context_text}"
                            ),
                        ),
                    ],
                    model=model,
                    # The claims are checked word for word against the draft,
                    # so the verifier must be asked for them (it was not), and
                    # needs room to copy them out (180 tokens truncated them).
                    max_tokens=1024,
                    temperature=0.0,
                    response_schema={
                        "type": "object",
                        "properties": {
                            "score": {"type": "number"},
                            "supported": {"type": "boolean"},
                            "verified_claims": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["score", "supported", "verified_claims"],
                    },
                )
            )
            try:
                payload = json.loads(response.content)
                if (
                    not isinstance(payload, dict)
                    or isinstance(payload.get("score"), bool)
                    or not isinstance(payload.get("score"), int | float)
                    or not isinstance(payload.get("supported"), bool)
                    or not isinstance(payload.get("verified_claims", []), list)
                    or not all(
                        isinstance(claim, str) for claim in payload.get("verified_claims", [])
                    )
                ):
                    raise TypeError("invalid verifier field types")
                candidate.score = max(0.0, min(1.0, float(payload["score"])))
                candidate.supported = payload["supported"]
                claims = payload.get("verified_claims", [])
                candidate.verified_claims = claims
            except (KeyError, TypeError, ValueError) as exc:
                raise RetrievalStrategyExecutionError(
                    self.strategy.value, "draft verification returned an invalid decision"
                ) from exc
            return candidate

        try:
            async with asyncio.TaskGroup() as task_group:
                verification_tasks = [
                    task_group.create_task(verify(candidate)) for candidate in candidates
                ]
        except BaseExceptionGroup as exc:
            raise first_task_group_exception(exc) from None
        verified = [task.result() for task in verification_tasks]
        supported = [candidate for candidate in verified if candidate.supported]
        if not supported:
            raise RetrievalStrategyExecutionError(
                self.strategy.value, "no speculative candidate was supported"
            )
        best = max(supported, key=lambda candidate: candidate.score)
        verified_claims = [claim.strip() for claim in best.verified_claims or [] if claim.strip()]

        verified_claims = _anchor_claims_to_draft(verified_claims, best.text)
        if not verified_claims:
            raise RetrievalStrategyExecutionError(
                self.strategy.value,
                "supported speculative candidate requires explicit verified claims",
            )
        result = _canonical_result(request, self.strategy, results, evidence)
        result = result.model_copy(update={"answer": best.text})
        return _extend_trace(
            result,
            [
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="speculative_verification",
                    status="complete",
                    detail={
                        "candidate_count": len(verified),
                        "supported_count": len(supported),
                        "drafted_from_evidence_subsets": len(subsets),
                        "verified_claims": verified_claims,
                        "scores": [candidate.score for candidate in verified],
                    },
                )
            ],
        )


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
        model: str = "",
        strict: bool = False,
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
                self._circuit_breakers[_cb_key] = CircuitBreaker(
                    failure_threshold=5,
                    cooldown_seconds=30,
                )
            cb: Any = self._circuit_breakers[_cb_key]
        except ImportError:
            cb = None

        # Step 1: Generate candidates sequentially to ensure deterministic provider ordering
        candidates: list[Candidate] = []
        for _ in range(self._n):
            if cb is not None and not cb.can_call():
                if strict:
                    raise RuntimeError("Speculative RAG provider circuit is open")
                break
            try:
                resp = await provider.complete(
                    CompletionRequest(
                        messages=[
                            Message(role="system", content=_CANDIDATE_SYSTEM),
                            Message(role="user", content=query),
                        ],
                        model=model,
                        max_tokens=max_tokens,
                        temperature=0.7,  # diversity
                    )
                )
                if cb is not None:
                    cb.record_success()
                text = (resp.content or "").strip()
                if text:
                    candidates.append(Candidate(text=text))
            except Exception:
                if cb is not None:
                    cb.record_failure()
                if strict:
                    raise

        if not candidates:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning(
                    "speculative_rag_failed",
                    error="no candidates generated",
                )
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
                    if strict:
                        raise
                    pass

            if not context:
                candidate.score = 0.3
                verified.append(candidate)
                continue

            if cb is not None and not cb.can_call():
                if strict:
                    raise RuntimeError("Speculative RAG provider circuit is open")
                candidate.score = 0.3
                verified.append(candidate)
                continue
            try:
                resp = await provider.complete(
                    CompletionRequest(
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
                        model=model,
                        max_tokens=100,
                        temperature=0.0,
                        response_schema={
                            "type": "object",
                            "properties": {
                                "score": {"type": "number"},
                                "supported": {"type": "boolean"},
                            },
                        },
                    )
                )
                if cb is not None:
                    cb.record_success()
                raw = (resp.content or "").strip()
                try:
                    d = json.loads(raw)
                    candidate.score = max(0.0, min(1.0, float(d.get("score", 0.3))))
                    candidate.supported = bool(
                        d.get("supported", candidate.score >= self._min_score)
                    )
                except Exception:
                    candidate.score = 0.3
            except Exception:
                if cb is not None:
                    cb.record_failure()
                if strict:
                    raise
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


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[\w'-]+")


def _anchor_claims_to_draft(claims: list[str], draft: str) -> list[str]:
    """Map each verifier claim onto the draft sentence it states; [] if any cannot be.

    The claims must come from the draft (the answer IS the draft), but a model
    copying them out often changes a word or the punctuation, and an exact
    substring check then rejected a correct, verified answer. A claim is anchored
    to the draft sentence it overlaps most (>= 80% of the claim's words), and the
    draft's own wording is kept. A claim that matches no sentence still rejects.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(draft) if s.strip()]
    normalized_draft = " ".join(draft.casefold().split())
    anchored: list[str] = []
    for claim in claims:
        norm = " ".join(claim.casefold().split()).rstrip(".!? ")
        if norm and norm in normalized_draft:
            anchored.append(claim)
            continue
        words = _terms(claim)
        numbers = {w for w in words if is_figure(w)}
        if not words:
            return []
        best_sentence, best_overlap = "", 0.0
        for sentence in sentences:
            sentence_words = _terms(sentence)
            if not numbers <= sentence_words:
                continue  # a claim's figures must all be in the sentence it anchors to
            overlap = len(words & sentence_words) / len(words)
            if overlap > best_overlap:
                best_sentence, best_overlap = sentence, overlap
        if best_overlap < 0.8:
            return []
        anchored.append(best_sentence)
    return list(dict.fromkeys(anchored))


_STOPWORDS = frozenset(
    [
        "the", "and", "for", "are", "was", "were", "has", "have", "had", "with", "from", "that",
        "this", "into", "per", "each", "any", "all", "its", "their", "our", "your", "his",
        "her", "not", "but", "can", "may", "will", "shall", "does", "did", "been", "being",
        "also", "than", "then", "there", "here", "which", "who", "whom", "what", "when",
        "where", "how"
    ]
)
_NUMBER_WORDS = frozenset(
    [
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
        "eighty", "ninety", "hundred", "thousand", "million", "billion", "half", "quarter",
        "first", "second", "third"
    ]
)


def is_figure(term: str) -> bool:
    return term[:1].isdigit() or term in _NUMBER_WORDS


def _terms(text: str) -> set[str]:
    """Content words (plural 's' stripped, stopwords dropped) and every figure —
    digits or number words — for claim anchoring."""
    out: set[str] = set()
    for token in _WORD.findall(text.casefold()):
        if is_figure(token):
            out.add(token)
        elif len(token) > 2 and token not in _STOPWORDS:
            out.add(token[:-1] if len(token) > 3 and token.endswith("s") else token)
    return out
