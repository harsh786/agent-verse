"""Thin synthesis layer over the tenant-aware retrieval gateway."""

from __future__ import annotations

import inspect
from typing import Any

from app.providers.base import CompletionRequest, Message
from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.rag.gateway import ResolvedLLM, RetrievalGateway
from app.tenancy.context import TenantContext


class RAGSynthesisError(RuntimeError):
    """Raised when retrieved evidence cannot be synthesized safely."""


class RAGRetriever:
    """Retrieve through one gateway, then optionally synthesize its citations."""

    def __init__(self, *, gateway: RetrievalGateway | Any | None = None) -> None:
        self._gateway = gateway

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
        if not synthesize or result.answer or not result.citations:
            return result

        answer = await self.synthesize(
            query=query,
            tenant_ctx=tenant_ctx,
            strategy=result.resolved_strategy_id,
            citations=result.citations,
            max_context_chars=max_context_chars,
        )
        return result.model_copy(update={"answer": answer, "grounded": bool(result.citations)})

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
