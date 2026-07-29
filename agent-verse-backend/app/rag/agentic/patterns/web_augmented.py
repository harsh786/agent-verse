"""Policy-controlled web evidence for grounding-aware RAG."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from app.net.ssrf_guard import assert_public_url
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.engine import RetrievalResult
from app.tenancy.context import TenantContext

_MAX_WEB_RESULTS = 8
_MAX_WEB_BYTES = 65_536
_MAX_WEB_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class WebSearchRequest:
    tenant_id: str
    query: str
    allowed_domains: tuple[str, ...]
    max_results: int
    max_bytes: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class WebEvidence:
    title: str
    url: str
    content: str
    fetched_at: datetime
    source: str


class SafeWebSearchCapability(Protocol):
    async def search(self, request: WebSearchRequest) -> list[WebEvidence]: ...


@dataclass(frozen=True, slots=True)
class WebPolicyDecision:
    allowed: bool
    reason: str
    allowed_domains: tuple[str, ...] = ()


async def resolve_web_policy(
    policy_services: tuple[object, ...],
    tenant_context: TenantContext,
) -> WebPolicyDecision:
    evaluators = [
        service
        for service in policy_services
        if callable(getattr(service, "evaluate", None))
    ]
    if not evaluators:
        return WebPolicyDecision(False, "web_policy_unavailable")

    allowed_domain_set: set[str] | None = None
    for service in evaluators:
        result = service.evaluate("web_search", tenant_ctx=tenant_context)  # type: ignore[attr-defined]
        if inspect.isawaitable(result):
            result = await result
        value = str(getattr(result, "value", result)).lower()
        if value != "allow":
            return WebPolicyDecision(False, "web_policy_denied")
        domain_resolver = getattr(service, "web_allowed_domains", None)
        if callable(domain_resolver):
            domains = domain_resolver(tenant_context)
            if inspect.isawaitable(domains):
                domains = await domains
            service_domains = {
                str(domain).lower().strip(".") for domain in domains if domain
            }
            if service_domains:
                allowed_domain_set = (
                    service_domains
                    if allowed_domain_set is None
                    else allowed_domain_set & service_domains
                )
                if not allowed_domain_set:
                    return WebPolicyDecision(False, "web_policy_denied")
    return WebPolicyDecision(
        True,
        "web_policy_allowed",
        tuple(sorted(allowed_domain_set or ())),
    )


def _domain_allowed(domain: str, allowed_domains: tuple[str, ...]) -> bool:
    return not allowed_domains or any(
        domain == allowed or domain.endswith(f".{allowed}") for allowed in allowed_domains
    )


async def retrieve_web_results(
    capability: object,
    *,
    tenant_context: TenantContext,
    query: str,
    top_k: int,
    policy: WebPolicyDecision,
) -> tuple[list[RetrievalResult], dict[str, Any]]:
    search = getattr(capability, "search", None)
    if not inspect.iscoroutinefunction(search):
        raise TypeError("A typed async web search capability is required")
    request = WebSearchRequest(
        tenant_id=tenant_context.tenant_id,
        query=query,
        allowed_domains=policy.allowed_domains,
        max_results=min(top_k, _MAX_WEB_RESULTS),
        max_bytes=_MAX_WEB_BYTES,
        timeout_seconds=_MAX_WEB_TIMEOUT_SECONDS,
    )
    deadline = asyncio.get_running_loop().time() + request.timeout_seconds
    async with asyncio.timeout_at(deadline):
        raw_results = await search(request)
    if not isinstance(raw_results, list) or not all(
        isinstance(item, WebEvidence) for item in raw_results
    ):
        raise TypeError("Web capability returned invalid evidence")

    accepted: list[RetrievalResult] = []
    bytes_used = 0
    rejected = 0
    now = datetime.now(UTC)
    for index, item in enumerate(raw_results[: request.max_results], start=1):
        domain = (urlparse(item.url).hostname or "").lower().strip(".")
        try:
            async with asyncio.timeout_at(deadline):
                await asyncio.to_thread(
                    assert_public_url,
                    item.url,
                    context="rag_web_evidence",
                )
        except ValueError:
            rejected += 1
            continue
        if not _domain_allowed(domain, policy.allowed_domains):
            rejected += 1
            continue
        encoded = item.content.encode("utf-8")
        remaining = request.max_bytes - bytes_used
        if remaining <= 0:
            break
        content = encoded[:remaining].decode("utf-8", errors="ignore")
        bytes_used += len(content.encode("utf-8"))
        if item.fetched_at.tzinfo is None:
            rejected += 1
            continue
        fetched_at = item.fetched_at.astimezone(UTC)
        accepted.append(
            RetrievalResult(
                chunk_id=f"web:{index}:{item.url}",
                content=content,
                score=max(0.5, 1.0 - (index * 0.05)),
                source_metadata={
                    "source_type": "web",
                    "source": item.source,
                    "title": item.title,
                    "url": item.url,
                    "source_url": item.url,
                    "domain": domain,
                    "fetched_at": fetched_at.isoformat(),
                    "freshness_seconds": max(0.0, (now - fetched_at).total_seconds()),
                },
                retrieval_legs=["web"],
                component_scores={"web": max(0.5, 1.0 - (index * 0.05))},
            )
        )
    return accepted, {
        "component": "web",
        "query": query,
        "result_count": len(accepted),
        "rejected_result_count": rejected,
        "bytes_used": bytes_used,
        "max_bytes": request.max_bytes,
        "timeout_seconds": request.timeout_seconds,
        "allowed_domains": list(policy.allowed_domains),
        "component_scores": {result.chunk_id: result.score for result in accepted},
    }


class WebAugmentedRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "web_augmented"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "Policy-authorized safe web evidence merged with persisted evidence."

    def is_compatible(self, goal_properties: Any) -> bool:
        return True
