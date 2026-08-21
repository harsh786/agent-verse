"""Bounded agentic RAG decisions over canonical retrieval primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar

from app.rag.agentic.fallback_chain import FallbackChain
from app.rag.contracts import (
    AgenticRAGRuntimeAdapter as AgenticRAGRuntimeContract,
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

_DECISION_SYSTEM = """Choose exactly one next Agentic RAG action.
Actions: retrieve, reformulate, fallback, stop.
Return JSON with action, reason, optional query or answer, and verified_claims.
Use stop only with a non-empty answer. When evidence supports the answer, include short
verbatim claims that occur in both the answer and evidence. Never invent another action."""

_MAX_DECISION_EVIDENCE_CHARS = 3_500

_FALLBACK_STRATEGIES = {
    "hybrid": RAGStrategy.HYBRID,
    "graph": RAGStrategy.GRAPH,
    "hyde": RAGStrategy.HYDE,
    "web": RAGStrategy.WEB_AUGMENTED,
}


class AgenticAction(StrEnum):
    RETRIEVE = "retrieve"
    REFORMULATE = "reformulate"
    FALLBACK = "fallback"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class AgenticDecision:
    action: AgenticAction
    reason: str
    query: str = ""
    answer: str = ""
    verified_claims: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, content: str) -> AgenticDecision:
        try:
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise TypeError("agentic decision must be an object")
            string_fields = ("action", "reason")
            optional_string_fields = ("query", "answer")
            if any(not isinstance(payload.get(field), str) for field in string_fields):
                raise TypeError("agentic decision fields must use declared types")
            if any(
                field in payload and not isinstance(payload[field], str)
                for field in optional_string_fields
            ):
                raise TypeError("agentic decision fields must use declared types")
            raw_claims = payload.get("verified_claims", [])
            if not isinstance(raw_claims, list) or not all(
                isinstance(claim, str) for claim in raw_claims
            ):
                raise TypeError("verified_claims must be a list of strings")
            decision = cls(
                action=AgenticAction(payload["action"]),
                reason=payload["reason"].strip(),
                query=payload.get("query", "").strip(),
                answer=payload.get("answer", "").strip(),
                verified_claims=tuple(claim.strip() for claim in raw_claims if claim.strip()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid agentic decision") from exc
        if not decision.reason:
            raise ValueError("Agentic decision reason is required")
        if decision.action is AgenticAction.REFORMULATE and not decision.query:
            raise ValueError("Reformulate decisions require a query")
        if decision.action is AgenticAction.STOP and not decision.answer:
            raise ValueError("Stop decisions require an answer")
        return decision


def _format_decision_evidence(contents: list[str]) -> str:
    from app.agent.sanitization import sanitize_tool_raw_output
    from app.intelligence.indirect_injection import scan_tool_output

    rendered = "\n\n".join(
        f"[{index}] {scan_tool_output(content, source='agentic_rag').sanitized_content}"
        for index, content in enumerate(contents, start=1)
    )
    return sanitize_tool_raw_output(rendered, max_length=_MAX_DECISION_EVIDENCE_CHARS)


def _claims_support_answer(
    decision: AgenticDecision,
    evidence_contents: list[str],
) -> bool:
    answer = decision.answer.casefold()
    evidence_text = "\n".join(evidence_contents).casefold()
    return bool(decision.verified_claims) and all(
        claim.casefold() in answer and claim.casefold() in evidence_text
        for claim in decision.verified_claims
    )


class AgenticRAGRuntimeAdapter(AgenticRAGRuntimeContract):
    """Execute typed retrieve/reformulate/fallback/stop decisions within a bound."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.AGENTIC

    def __init__(self, max_iterations: int = 6) -> None:
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
            execute_core_strategy,
        )

        if context is None or context.llm is None or context.llm.provider is None:
            raise RetrievalStrategyExecutionError(self.strategy.value, "resolved LLM is required")

        provider = context.llm.provider
        model = context.llm.model
        current_query = request.query
        answer = ""
        results: list[RetrievalResult] = []
        evidence: list[dict[str, Any]] = []
        trace: list[RAGStrategyTrace] = []
        fallback_results: list[RAGExecutionResult] = []
        fallback_chain = FallbackChain()
        current_source = "agentic"
        answer_is_supported = False

        for iteration in range(self._max_iterations):
            evidence_contents = [result.content for result in results]
            evidence_contents.extend(
                citation.content
                for fallback_result in fallback_results
                for citation in fallback_result.citations
            )
            decision_evidence = _format_decision_evidence(evidence_contents)
            response = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_DECISION_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Question: {request.query}\n"
                                f"Current query: {current_query}\n"
                                f"Evidence count: {len(results)}\n"
                                f"Iteration: {iteration}\n"
                                f"Evidence:\n{decision_evidence or '[none]'}"
                            ),
                        ),
                    ],
                    model=model,
                    max_tokens=180,
                    temperature=0.0,
                    response_schema={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "reason": {"type": "string"},
                            "query": {"type": "string"},
                            "answer": {"type": "string"},
                            "verified_claims": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                )
            )
            try:
                decision = AgenticDecision.from_json(response.content)
            except ValueError as exc:
                raise RetrievalStrategyExecutionError(
                    self.strategy.value, "LLM returned an invalid typed decision"
                ) from exc
            trace.append(
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="agentic_decision",
                    status="complete",
                    detail={
                        "iteration": iteration,
                        "action": decision.action.value,
                        "reason": decision.reason,
                        "query": decision.query,
                    },
                )
            )

            if decision.action is AgenticAction.STOP:
                answer = decision.answer
                answer_is_supported = _claims_support_answer(
                    decision,
                    evidence_contents,
                )
                break
            if decision.action is AgenticAction.REFORMULATE:
                current_query = decision.query
                continue
            if decision.action is AgenticAction.FALLBACK:
                available_infra = {
                    infra
                    for strategy, infra in (
                        (RAGStrategy.GRAPH, "kg_store"),
                        (RAGStrategy.WEB_AUGMENTED, "web_search"),
                    )
                    if strategy in context.dependencies.available_strategies
                }
                fallback = fallback_chain.next_decision(
                    current_source,
                    reason=decision.reason,
                    available_infra=available_infra,
                )
                if fallback is None:
                    raise RetrievalStrategyExecutionError(
                        self.strategy.value, "no bounded fallback source is available"
                    )
                current_source = fallback.source
                fallback_strategy = _FALLBACK_STRATEGIES.get(current_source)
                if (
                    fallback_strategy is None
                    or fallback_strategy not in context.dependencies.available_strategies
                ):
                    raise RetrievalStrategyExecutionError(
                        self.strategy.value,
                        f"fallback capability is unavailable: {current_source}",
                    )
                fallback_dependencies = replace(
                    context.dependencies,
                    llm=context.dependencies.strategy_llms.get(fallback_strategy, context.llm),
                )
                fallback_context = replace(
                    context,
                    strategy=fallback_strategy,
                    dependencies=fallback_dependencies,
                )
                fallback_result = await execute_core_strategy(
                    fallback_strategy,
                    request,
                    fallback_context,
                )
                fallback_results.append(fallback_result)
                trace.extend(fallback_result.strategy_trace)
                continue

            embedding = await _embed_text(context, current_query, self.strategy)
            attempt_evidence: list[dict[str, Any]] = []
            retrieved = await _search_persisted(
                context,
                request,
                query=current_query,
                embedding=embedding,
                retrieval_mode="hybrid",
                evidence=attempt_evidence,
            )
            for item in attempt_evidence:
                item.update(
                    {
                        "iteration": iteration,
                        "agentic_action": decision.action.value,
                        "fallback_source": None,
                    }
                )
            evidence.extend(attempt_evidence)
            results = merge_grounding_results([results, retrieved], top_k=request.top_k)
        else:
            trace.append(
                RAGStrategyTrace(
                    strategy=self.strategy,
                    action="agentic_stop",
                    status="complete",
                    detail={
                        "stop_reason": "max_iterations_reached",
                        "max_iterations": self._max_iterations,
                    },
                )
            )

        result = _canonical_result(request, self.strategy, results, evidence)
        fallback_citations = [
            citation
            for fallback_result in fallback_results
            for citation in fallback_result.citations
        ]
        fallback_legs = [
            leg for fallback_result in fallback_results for leg in fallback_result.retrieval_legs
        ]
        result = result.model_copy(
            update={
                "answer": answer,
                "citations": [*result.citations, *fallback_citations],
                "retrieval_legs": [*result.retrieval_legs, *fallback_legs],
                "grounded": answer_is_supported,
            }
        )
        return _extend_trace(result, trace)
