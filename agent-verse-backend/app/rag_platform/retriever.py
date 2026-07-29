"""Thin synthesis layer over the tenant-aware retrieval gateway."""

from __future__ import annotations

import inspect
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

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
    """Verify citation-marker-scoped claims conservatively."""

    _MARKER_GROUP = re.compile(r"(?:\[(?:\d+(?:\s*,\s*\d+)*)\]\s*)+")

    def __init__(self, *, provider: Any = None, model: str = "") -> None:
        self.provider = provider
        self.model = model.strip()

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"\[(?:\d+(?:\s*,\s*\d+)*)\]", "", normalized)
        normalized = " ".join(normalized.split()).strip()
        segments = re.split(r"(https?://\S+)", normalized, flags=re.IGNORECASE)
        normalized = "".join(
            segment
            if re.fullmatch(r"https?://\S+", segment, flags=re.IGNORECASE)
            else segment.casefold()
            for segment in segments
        )
        if not re.search(r"https?://\S+$", normalized, flags=re.IGNORECASE):
            normalized = normalized.rstrip(".!?").rstrip()
        return normalized

    @classmethod
    def _atomic_claims(cls, answer: str) -> list[tuple[str, list[int]]]:
        atomic: list[tuple[str, list[int]]] = []
        cursor = 0
        for marker in cls._MARKER_GROUP.finditer(answer):
            scoped = answer[cursor : marker.start()]
            scoped = re.sub(
                r"^[\s,;:.!?]+(?:and\s+|but\s+)?",
                "",
                scoped,
                flags=re.IGNORECASE,
            ).strip()
            scoped = re.sub(
                r"^(?:and|but)\s+",
                "",
                scoped,
                flags=re.IGNORECASE,
            )
            references = [int(value) for value in re.findall(r"\d+", marker.group())]
            if cls._normalize(scoped):
                atomic.append((scoped, list(references)))
            cursor = marker.end()
        trailing = re.sub(r"^[\s,;:.!?]+", "", answer[cursor:]).strip()
        if cls._normalize(trailing):
            atomic.append((trailing, []))
        return atomic

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
                or (parsed["supported"] is True and parsed["reason"] != "entailed")
                or (
                    parsed["supported"] is False
                    and parsed["reason"] != "not_entailed"
                )
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
        for claim, references in self._atomic_claims(answer):
            claim_normalized = self._normalize(claim)
            if not claim_normalized:
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
            evidence_normalized = self._normalize(evidence)
            if claim_normalized == evidence_normalized:
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
        result = result.model_copy(update={"answer": answer})
        return await self.verify_result(result, tenant_ctx=tenant_ctx)

    async def verify_result(
        self,
        result: RAGExecutionResult,
        *,
        tenant_ctx: TenantContext,
    ) -> RAGExecutionResult:
        """Apply the same typed citation verification to any synthesized result."""

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
                result.answer,
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
                "answer": result.answer,
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
