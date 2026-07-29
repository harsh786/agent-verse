"""Thin synthesis layer over the tenant-aware retrieval gateway."""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar

from app.providers.base import CompletionRequest, Message
from app.rag.contracts import (
    RAGCitation,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.gateway import ResolvedLLM, RetrievalGateway
from app.tenancy.context import TenantContext


class RAGSynthesisError(RuntimeError):
    """Raised when retrieved evidence cannot be synthesized safely."""


@dataclass(frozen=True, slots=True)
class CitationVerification:
    grounded: bool
    unsupported_claims: list[str]
    reason: str


class MinimalCitationVerifier:
    """Verify each cited claim has deterministic lexical support in its evidence."""

    _STOP_WORDS = frozenset(
        {"a", "an", "and", "are", "as", "at", "be", "is", "of", "or", "the", "to"}
    )
    _SYNONYMS: ClassVar[dict[str, str]] = {
        "allows": "allow",
        "allowed": "allow",
        "permit": "allow",
        "permits": "allow",
        "permitted": "allow",
        "prohibits": "prohibit",
        "prohibited": "prohibit",
        "forbids": "prohibit",
        "forbidden": "prohibit",
        "mandatory": "required",
        "requires": "required",
    }

    def __init__(self, *, provider: Any = None, model: str = "") -> None:
        self.provider = provider
        self.model = model.strip()

    def _tokens(self, text: str) -> set[str]:
        return {
            self._SYNONYMS.get(token, token)
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) > 2 and token not in self._STOP_WORDS
        }

    @staticmethod
    def _contradiction(claim: str, evidence: str) -> bool:
        claim_lower = claim.lower()
        evidence_lower = evidence.lower()
        claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", claim_lower))
        evidence_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", evidence_lower))
        if claim_numbers and evidence_numbers and claim_numbers != evidence_numbers:
            return True
        allow_words = ("allow", "permit")
        prohibit_words = ("prohibit", "forbid", "deny", "ban")
        claim_allows = any(word in claim_lower for word in allow_words)
        evidence_allows = any(word in evidence_lower for word in allow_words)
        claim_prohibits = any(word in claim_lower for word in prohibit_words)
        evidence_prohibits = any(word in evidence_lower for word in prohibit_words)
        if (claim_allows and evidence_prohibits) or (
            claim_prohibits and evidence_allows
        ):
            return True
        claim_not_required = bool(re.search(r"\bnot\s+required\b", claim_lower))
        evidence_not_required = bool(
            re.search(r"\bnot\s+required\b", evidence_lower)
        )
        claim_required = "required" in claim_lower and not claim_not_required
        evidence_required = "required" in evidence_lower and not evidence_not_required
        return (claim_required and evidence_not_required) or (
            claim_not_required and evidence_required
        )

    async def _provider_entails(self, claim: str, evidence: str) -> CitationVerification:
        if self.provider is None or not self.model:
            return CitationVerification(False, [claim], "unsupported")
        schema = {
            "type": "object",
            "properties": {
                "supported": {"type": "boolean"},
                "reason": {
                    "type": "string",
                    "enum": ["entailed", "not_entailed"],
                },
            },
            "required": ["supported", "reason"],
            "additionalProperties": False,
        }
        try:
            response = await self.provider.complete(
                CompletionRequest(
                    messages=[
                        Message(
                            role="user",
                            content=(
                                "Determine whether the claim is fully entailed by the evidence. "
                                "Return only the requested JSON fields.\n\n"
                                f"Claim: {claim}\nEvidence: {evidence}"
                            ),
                        )
                    ],
                    model=self.model,
                    max_tokens=100,
                    response_schema=schema,
                )
            )
            parsed = json.loads(str(response.content))
            if (
                not isinstance(parsed, dict)
                or set(parsed) != {"supported", "reason"}
                or not isinstance(parsed["supported"], bool)
                or parsed["reason"] not in {"entailed", "not_entailed"}
            ):
                raise ValueError("Invalid entailment response")
        except Exception:
            return CitationVerification(False, [claim], "verifier_failure")
        return CitationVerification(
            bool(parsed["supported"]),
            [] if parsed["supported"] else [claim],
            "supported" if parsed["supported"] else "unsupported",
        )

    async def verify(
        self,
        answer: str,
        citations: list[RAGCitation],
    ) -> CitationVerification:
        unsupported: list[str] = []
        reasons: list[str] = []
        checked = 0
        for sentence in re.split(r"(?<=[.!?])\s+", answer.strip()):
            references = [int(value) for value in re.findall(r"\[(\d+)\]", sentence)]
            claim = re.sub(r"\[\d+\]", "", sentence).strip(" .")
            claim_tokens = self._tokens(claim)
            if not claim_tokens:
                continue
            checked += 1
            if not references or any(
                reference < 1 or reference > len(citations)
                for reference in references
            ):
                unsupported.append(claim)
                reasons.append("invalid_citation")
                continue
            evidence = " ".join(citations[index - 1].content for index in references)
            if self._contradiction(claim, evidence):
                unsupported.append(claim)
                reasons.append("contradiction")
                continue
            evidence_tokens = self._tokens(evidence)
            support = len(claim_tokens & evidence_tokens) / len(claim_tokens)
            if support >= 0.6:
                continue
            entailment = await self._provider_entails(claim, evidence)
            if not entailment.grounded:
                unsupported.extend(entailment.unsupported_claims)
                reasons.append(entailment.reason)
        reason = (
            "supported"
            if checked > 0 and not unsupported
            else "contradiction"
            if "contradiction" in reasons
            else "invalid_citation"
            if "invalid_citation" in reasons
            else "verifier_failure"
            if "verifier_failure" in reasons
            else "unsupported"
        )
        return CitationVerification(
            grounded=checked > 0 and not unsupported,
            unsupported_claims=(
                unsupported if unsupported else [] if checked > 0 else ["No claims verified"]
            ),
            reason=reason,
        )


class RAGRetriever:
    """Retrieve through one gateway, then optionally synthesize its citations."""

    def __init__(
        self,
        *,
        gateway: RetrievalGateway | Any | None = None,
        citation_verifier: Any | None = None,
    ) -> None:
        self._gateway = gateway
        self._citation_verifier = citation_verifier or MinimalCitationVerifier()

    def set_gateway(self, gateway: RetrievalGateway | Any) -> None:
        """Set the injected gateway for application assembly and tests."""

        self._gateway = gateway

    async def retrieve(
        self,
        query: str,
        tenant_ctx: TenantContext,
        collection_id: str | None = None,
        strategy: str | RAGStrategy = RAGStrategy.HYBRID,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        *,
        synthesize: bool = True,
        max_context_chars: int = 6000,
    ) -> RAGExecutionResult:
        """Execute canonical retrieval without alternate or fallback algorithms."""

        if self._gateway is None:
            raise RuntimeError("Retrieval gateway is not configured")
        if not collection_id:
            raise ValueError("collection_id is required")

        result = await self._gateway.execute(
            tenant_ctx,
            collection_id=collection_id,
            query=query,
            strategy_id=strategy,
            top_k=top_k,
            filters=filters or {},
        )
        if not synthesize:
            return result
        answer = result.answer
        if not answer and result.citations:
            answer = await self.synthesize(
                query=query,
                tenant_ctx=tenant_ctx,
                strategy=result.resolved_strategy_id,
                citations=result.citations,
                max_context_chars=max_context_chars,
            )
        trace = list(result.strategy_trace)
        try:
            verifier = self._citation_verifier
            if isinstance(verifier, MinimalCitationVerifier) and verifier.provider is None:
                try:
                    resolved = await self._resolve_llm(
                        tenant_ctx,
                        result.resolved_strategy_id,
                    )
                    verifier = MinimalCitationVerifier(
                        provider=resolved.provider,
                        model=resolved.model,
                    )
                except RAGSynthesisError:
                    pass
            verification = await verifier.verify(
                answer,
                result.citations,
            )
            trace.append(
                RAGStrategyTrace(
                    strategy=result.resolved_strategy_id,
                    action="citation_verification",
                    status="complete",
                    detail={
                        "unsupported_claims": list(
                            verification.unsupported_claims
                        ),
                        "reason": str(getattr(verification, "reason", "unsupported")),
                    },
                )
            )
            grounded = bool(verification.grounded)
        except Exception:
            trace.append(
                RAGStrategyTrace(
                    strategy=result.resolved_strategy_id,
                    action="citation_verification",
                    status="failed",
                    detail={"reason": "citation_verifier_unavailable"},
                )
            )
            grounded = False
        return result.model_copy(
            update={
                "answer": answer,
                "grounded": grounded,
                "strategy_trace": trace,
            }
        )

    async def synthesize(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        strategy: RAGStrategy,
        citations: list[RAGCitation],
        max_context_chars: int = 6000,
    ) -> str:
        """Synthesize canonical citations with the tenant's configured provider/model."""

        resolved = await self._resolve_llm(tenant_ctx, strategy)
        provider: Any = resolved.provider
        if provider is None:
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        context_parts: list[str] = []
        context_length = 0
        for index, citation in enumerate(citations, start=1):
            part = f"[{index}] {citation.content}"
            if context_length + len(part) > max_context_chars:
                break
            context_parts.append(part)
            context_length += len(part)
        if not context_parts:
            raise RAGSynthesisError("No retrieved evidence fits the synthesis context")
        context = "\n\n".join(context_parts)

        try:
            response = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "Answer using only the supplied evidence. Cite supporting "
                                "evidence with [N]. If the evidence is insufficient, say so.\n\n"
                                f"Evidence:\n{context}"
                            ),
                        ),
                        Message(role="user", content=query),
                    ],
                    model=resolved.model,
                    max_tokens=1200,
                )
            )
        except Exception as exc:
            raise RAGSynthesisError("Answer synthesis failed") from exc
        answer = str(response.content).strip()
        if not answer:
            raise RAGSynthesisError("Answer synthesis returned no content")
        return answer

    async def _resolve_llm(
        self,
        tenant_ctx: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM:
        dependencies = getattr(self._gateway, "dependencies", None)
        resolver = getattr(dependencies, "llm_resolver", None)
        if resolver is None:
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        try:
            resolved = resolver(tenant_ctx, strategy)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        except Exception as exc:
            raise RAGSynthesisError("Tenant LLM provider is unavailable") from exc
        if not isinstance(resolved, ResolvedLLM) or not resolved.model.strip():
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        provider = resolved.provider
        if provider is None:
            raise RAGSynthesisError("Tenant LLM provider is unavailable")
        return resolved


# Kept for callers that configure a process-local singleton explicitly.
rag_retriever = RAGRetriever()
