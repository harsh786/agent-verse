"""FLARE — Forward-Looking Active Retrieval Augmented Generation.

Jiang et al. 2023: 'Active Retrieval Augmented Generation'

Algorithm:
  1. Generate an initial response
  2. Detect uncertainty signals (hedging phrases, [UNCERTAIN] markers, low-confidence text)
  3. If uncertain: extract uncertain claim, retrieve context for it
  4. Re-generate with retrieved context
  5. Repeat up to max_iterations

Uncertainty signals: "I think", "I'm not sure", "might be", "could be",
"possibly", "I believe", "[UNCERTAIN]", "I don't know", "unclear"
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.contracts import (
    FLARERAGRuntimeAdapter as FLARERAGRuntimeContract,
)
from app.rag.contracts import (
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.engine import (
    RetrievalResult,
    RetrievalStrategyExecutionError,
    merge_grounding_results,
)

_UNCERTAINTY_SIGNALS = frozenset(
    {
        "i think",
        "i'm not sure",
        "i am not sure",
        "might be",
        "could be",
        "possibly",
        "i believe",
        "unclear",
        "uncertain",
        "not certain",
        "may be",
        "perhaps",
        "i don't know",
        "i do not know",
        "[uncertain]",
        "i'm unsure",
        "hard to say",
        "not clear",
    }
)

_FLARE_GENERATE_SYSTEM = """Answer the question as accurately as possible.
If you are uncertain about any part, indicate uncertainty explicitly with phrases like
'I'm not sure' or '[UNCERTAIN]'. Be honest about what you don't know."""

_FLARE_REFINE_SYSTEM = (
    "Using the provided context, answer the question accurately and confidently.\n"
    "Base your answer on the context. Do not express uncertainty about information "
    "given in the context."
)

_FLARE_FOLLOW_UP_SYSTEM = """Rewrite the uncertain span as one standalone factual search
question. Return only the new question. Do not repeat hedging language or answer it."""


def _detect_uncertainty(text: str) -> bool:
    """Returns True if the text contains uncertainty signals."""
    lower = text.lower()
    return any(signal in lower for signal in _UNCERTAINTY_SIGNALS)


def _extract_uncertain_claim(text: str) -> str:
    """Extract the uncertain portion of text for targeted retrieval."""
    sentences = re.split(r"[.!?]", text)
    for sentence in sentences:
        if _detect_uncertainty(sentence) and sentence.strip():
            return sentence.strip()
    return text[:200]


class FLARERAGRuntimeAdapter(FLARERAGRuntimeContract):
    """Canonical FLARE adapter with independently embedded follow-up retrieval."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.FLARE

    def __init__(self, max_iterations: int = 2) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self._max_iterations = max_iterations

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
        response = await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=_FLARE_GENERATE_SYSTEM),
                    Message(role="user", content=request.query),
                ],
                model=model,
                max_tokens=800,
                temperature=0.3,
            )
        )
        answer = response.content.strip()
        evidence: list[dict[str, Any]] = []
        retained: list[RetrievalResult] = []
        follow_ups: list[dict[str, Any]] = []

        for iteration in range(self._max_iterations):
            if not _detect_uncertainty(answer):
                break
            uncertain_span = _extract_uncertain_claim(answer)
            follow_up_response = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_FLARE_FOLLOW_UP_SYSTEM),
                        Message(role="user", content=uncertain_span),
                    ],
                    model=model,
                    max_tokens=120,
                    temperature=0.0,
                )
            )
            follow_up = follow_up_response.content.strip()
            if not follow_up or follow_up == uncertain_span:
                raise RetrievalStrategyExecutionError(
                    self.strategy.value, "follow-up generation did not create new text"
                )
            embedding = await _embed_text(context, follow_up, self.strategy)
            follow_up_evidence: list[dict[str, Any]] = []
            retrieved = await _search_persisted(
                context,
                request,
                query=follow_up,
                embedding=embedding,
                retrieval_mode="hybrid",
                evidence=follow_up_evidence,
            )
            for item in follow_up_evidence:
                item.update(
                    {
                        "iteration": iteration,
                        "uncertain_span": uncertain_span,
                        "follow_up": follow_up,
                    }
                )
            evidence.extend(follow_up_evidence)
            retained = merge_grounding_results(
                [retained, retrieved],
                top_k=request.top_k,
            )
            follow_ups.append(
                {
                    "iteration": iteration,
                    "uncertain_span": uncertain_span,
                    "follow_up": follow_up,
                    "result_count": len(retrieved),
                }
            )
            context_text = "\n\n".join(item.content for item in retrieved)
            continuation = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_FLARE_REFINE_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Question: {request.query}\n"
                                f"Draft so far: {answer}\n"
                                f"Follow-up: {follow_up}\n"
                                f"Context:\n{context_text}\n\n"
                                "Continue the answer with the uncertainty resolved."
                            ),
                        ),
                    ],
                    model=model,
                    max_tokens=800,
                    temperature=0.0,
                )
            )
            answer = continuation.content.strip()

        result = _canonical_result(request, self.strategy, retained, evidence)
        result = result.model_copy(update={"answer": answer})
        return _extend_trace(
            result,
            [
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="flare_follow_up",
                    status="complete",
                    detail={
                        "follow_ups": follow_ups,
                        "stop_reason": (
                            "uncertainty_resolved"
                            if not _detect_uncertainty(answer)
                            else "max_iterations_reached"
                        ),
                    },
                )
            ],
        )


class FLAREPattern(RAGPattern):
    """FLARE: generate → detect uncertainty → retrieve → re-generate."""

    def __init__(self, max_iterations: int = 2) -> None:
        self._max_iter = max_iterations
        self._circuit_breakers: dict[str, Any] = {}

    @property
    def pattern_id(self) -> str:
        return "flare"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "FLARE: Forward-Looking Active Retrieval — generate response, detect uncertainty "
            "signals, retrieve targeted context for uncertain claims, re-generate with evidence. "
            "Based on Jiang et al. 2023."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings

            if not get_settings().enable_flare:
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
        system_prompt: str = _FLARE_GENERATE_SYSTEM,
        max_tokens: int = 800,
        model: str = "",
        strict: bool = False,
        **kwargs: Any,
    ) -> str:
        """Execute FLARE: generate → check uncertainty → retrieve → refine."""
        try:
            from app.observability.logging import get_logger

            get_logger(__name__).info("flare_started", query=query[:60])
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

        # Step 1: Initial generation
        if cb is not None and not cb.can_call():
            if strict:
                raise RuntimeError("FLARE provider circuit is open")
            return ""
        try:
            resp = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=system_prompt),
                        Message(role="user", content=query),
                    ],
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
            )
            if cb is not None:
                cb.record_success()
            initial = (resp.content or "").strip()
        except Exception:
            if cb is not None:
                cb.record_failure()
            if strict:
                raise
            return ""

        # Step 2: Check for uncertainty
        if not _detect_uncertainty(initial) or retrieve_fn is None:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).info("flare_completed", result_len=len(initial))
            except Exception:
                pass
            return initial

        # Step 3: Retrieve context for uncertain claim
        uncertain_claim = _extract_uncertain_claim(initial)
        for _ in range(self._max_iter):
            try:
                context = await retrieve_fn(uncertain_claim)
            except Exception:
                if strict:
                    raise
                break

            if not context:
                break

            # Step 4: Re-generate with context
            if cb is not None and not cb.can_call():
                if strict:
                    raise RuntimeError("FLARE provider circuit is open")
                break
            try:
                refined_resp = await provider.complete(
                    CompletionRequest(
                        messages=[
                            Message(role="system", content=_FLARE_REFINE_SYSTEM),
                            Message(
                                role="user",
                                content=(
                                    f"Context:\n{context[:1500]}\n\n"
                                    f"Question: {query}\n\n"
                                    "Answer based on the context:"
                                ),
                            ),
                        ],
                        model=model,
                        max_tokens=max_tokens,
                        temperature=0.0,
                    )
                )
                if cb is not None:
                    cb.record_success()
                refined = (refined_resp.content or "").strip()
                if refined and not _detect_uncertainty(refined):
                    try:
                        from app.observability.logging import get_logger

                        get_logger(__name__).info("flare_completed", result_len=len(refined))
                    except Exception:
                        pass
                    return refined
                if refined:
                    initial = refined
                    uncertain_claim = _extract_uncertain_claim(refined)
            except Exception:
                if cb is not None:
                    cb.record_failure()
                if strict:
                    raise
                break

        try:
            from app.observability.logging import get_logger

            get_logger(__name__).info("flare_completed", result_len=len(initial))
        except Exception:
            pass
        return initial
