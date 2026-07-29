"""Policy-controlled web evidence for grounding-aware RAG."""

from __future__ import annotations

import asyncio
import html
import inspect
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse

import httpx

from app.net.ssrf_guard import assert_public_url
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.engine import RetrievalResult
from app.tenancy.context import TenantContext
from app.tools.web_search import SearchResult, WebSearchTool

_MAX_WEB_RESULTS = 8
_MAX_WEB_BYTES = 65_536
_MAX_WEB_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class WebSearchRequest:
    tenant_context: TenantContext
    query: str
    allowed_domains: tuple[str, ...]
    max_results: int
    max_bytes: int
    timeout_seconds: float

    @property
    def tenant_id(self) -> str:
        return self.tenant_context.tenant_id


@dataclass(frozen=True, slots=True)
class WebEvidence:
    title: str
    url: str
    content: str
    fetched_at: datetime
    source: str
    domain: str = ""
    freshness_seconds: float = 0.0


@runtime_checkable
class SafeWebSearchCapability(Protocol):
    configured: bool

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
                str(domain).strip().lower().strip(".") for domain in domains if domain
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


def _combine_domain_constraints(
    *constraints: tuple[str, ...],
) -> tuple[str, ...]:
    constrained = [set(items) for items in constraints if items]
    if not constrained:
        return ()
    allowed = constrained[0]
    for domains in constrained[1:]:
        allowed &= domains
    return tuple(sorted(allowed))


def _html_to_text(content: str) -> str:
    without_markup = re.sub(r"<[^>]+>", " ", content)
    return " ".join(html.unescape(without_markup).split())


class GovernedWebSearchCapability:
    """Policy-gated SearXNG search with SSRF-safe result fetching."""

    configured = True
    _MAX_REDIRECTS = 3

    def __init__(
        self,
        *,
        backend: WebSearchTool,
        policy_services: tuple[object, ...],
        default_allowed_domains: tuple[str, ...] = (),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not backend.configured:
            raise ValueError("A configured SearXNG backend is required")
        self._backend = backend
        self._policy_services = policy_services
        self._default_allowed_domains = tuple(
            sorted({domain.lower().strip(".") for domain in default_allowed_domains if domain})
        )
        self._transport = transport

    async def _validate_url(self, url: str, allowed_domains: tuple[str, ...]) -> str:
        domain = (urlparse(url).hostname or "").lower().strip(".")
        if not _domain_allowed(domain, allowed_domains):
            raise ValueError("URL domain is not allowlisted")
        await asyncio.to_thread(assert_public_url, url, context="rag_web_fetch")
        return domain

    async def _fetch_result(
        self,
        result: SearchResult,
        *,
        allowed_domains: tuple[str, ...],
        max_bytes: int,
        deadline: float,
    ) -> WebEvidence:
        current_url = result.url
        async with httpx.AsyncClient(
            timeout=max(0.1, deadline - asyncio.get_running_loop().time()),
            follow_redirects=False,
            transport=self._transport,
            headers={"User-Agent": "AgentVerse-RAG/1.0"},
        ) as client:
            for redirect_count in range(self._MAX_REDIRECTS + 1):
                async with asyncio.timeout_at(deadline):
                    domain = await self._validate_url(current_url, allowed_domains)
                    async with client.stream("GET", current_url) as response:
                        if response.is_redirect:
                            if redirect_count >= self._MAX_REDIRECTS:
                                raise ValueError("Web result exceeded redirect limit")
                            location = response.headers.get("location", "")
                            if not location:
                                raise ValueError("Web result redirect had no location")
                            current_url = urljoin(current_url, location)
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if not any(
                            allowed in content_type
                            for allowed in ("text/", "application/json", "application/xhtml")
                        ):
                            raise ValueError("Web result content type is not textual")
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            body.extend(chunk)
                            if len(body) >= max_bytes:
                                del body[max_bytes:]
                                break
                content = bytes(body).decode("utf-8", errors="ignore")
                if "html" in content_type or "xhtml" in content_type:
                    content = _html_to_text(content)
                fetched_at = datetime.now(UTC)
                return WebEvidence(
                    title=result.title,
                    url=current_url,
                    content=content,
                    fetched_at=fetched_at,
                    source=result.source or "searxng",
                    domain=domain,
                    freshness_seconds=0.0,
                )
        raise ValueError("Web result fetch did not complete")

    async def search(self, request: WebSearchRequest) -> list[WebEvidence]:
        policy = await resolve_web_policy(self._policy_services, request.tenant_context)
        if not policy.allowed:
            raise PermissionError(policy.reason)
        allowed_domains = _combine_domain_constraints(
            policy.allowed_domains,
            request.allowed_domains,
            self._default_allowed_domains,
        )
        if any(
            constraint and not allowed_domains
            for constraint in (
                policy.allowed_domains,
                request.allowed_domains,
                self._default_allowed_domains,
            )
        ):
            raise PermissionError("web_policy_denied")
        max_results = max(1, min(request.max_results, _MAX_WEB_RESULTS))
        max_bytes = max(1, min(request.max_bytes, _MAX_WEB_BYTES))
        timeout_seconds = max(0.1, min(request.timeout_seconds, _MAX_WEB_TIMEOUT_SECONDS))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with asyncio.timeout_at(deadline):
            search_result = await self._backend.search(
                request.query,
                num_results=max_results,
            )
        if search_result.error:
            return []

        evidence: list[WebEvidence] = []
        remaining_bytes = max_bytes
        for result in search_result.results[:max_results]:
            if remaining_bytes <= 0:
                break
            try:
                item = await self._fetch_result(
                    result,
                    allowed_domains=allowed_domains,
                    max_bytes=remaining_bytes,
                    deadline=deadline,
                )
            except (httpx.HTTPError, TimeoutError, ValueError):
                continue
            item_size = len(item.content.encode("utf-8"))
            if item_size == 0:
                continue
            evidence.append(item)
            remaining_bytes -= item_size
        return evidence


def build_safe_web_search_capability(
    *,
    searxng_url: str,
    policy_services: tuple[object, ...],
    allowed_domains: tuple[str, ...] = (),
    transport: httpx.AsyncBaseTransport | None = None,
) -> SafeWebSearchCapability | None:
    if not searxng_url.strip():
        return None
    backend = WebSearchTool(
        searxng_url=searxng_url,
        timeout_seconds=_MAX_WEB_TIMEOUT_SECONDS,
        max_response_bytes=_MAX_WEB_BYTES,
        fallback_to_duckduckgo=False,
        transport=transport,
    )
    return GovernedWebSearchCapability(
        backend=backend,
        policy_services=policy_services,
        default_allowed_domains=allowed_domains,
        transport=transport,
    )


def parse_allowed_domains(value: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                domain.strip().lower().strip(".")
                for domain in value.split(",")
                if domain.strip()
            }
        )
    )


async def retrieve_web_results(
    capability: SafeWebSearchCapability,
    *,
    tenant_context: TenantContext,
    query: str,
    top_k: int,
    policy: WebPolicyDecision,
) -> tuple[list[RetrievalResult], dict[str, Any]]:
    request = WebSearchRequest(
        tenant_context=tenant_context,
        query=query,
        allowed_domains=policy.allowed_domains,
        max_results=min(top_k, _MAX_WEB_RESULTS),
        max_bytes=_MAX_WEB_BYTES,
        timeout_seconds=_MAX_WEB_TIMEOUT_SECONDS,
    )
    deadline = asyncio.get_running_loop().time() + request.timeout_seconds
    async with asyncio.timeout_at(deadline):
        raw_results = await capability.search(request)
    if not isinstance(raw_results, list) or not all(
        isinstance(item, WebEvidence) for item in raw_results
    ):
        raise TypeError("Web capability returned invalid evidence")

    accepted: list[RetrievalResult] = []
    bytes_used = 0
    rejected = 0
    now = datetime.now(UTC)
    for index, item in enumerate(raw_results[: request.max_results], start=1):
        domain = item.domain or (urlparse(item.url).hostname or "").lower().strip(".")
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
                    "freshness_seconds": max(
                        item.freshness_seconds,
                        (now - fetched_at).total_seconds(),
                        0.0,
                    ),
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
