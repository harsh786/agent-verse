"""P2-6: /rag/strategies readiness reflects real capability.

Live P0 KB-STRATEGIES: raptor and agentic_chunking were listed available=True
(their precomputed index was never checked: ``readiness_all`` ran with no
collection) and web_augmented was available=True while SearXNG was
unreachable; all three then answered every query with a 503.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.rag.agentic.patterns.web_augmented import (
    GovernedWebSearchCapability,
    WebSearchCapabilityError,
    WebSearchReport,
    WebSearchRequest,
)
from app.rag.contracts import RAGStrategy
from app.rag.gateway import (
    KnowledgeStoreCollectionAuthorizer,
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalGateway,
    core_strategy_capabilities,
)
from app.tenancy.context import TenantContext
from app.tools.web_search import WebSearchTool
from tests.rag.test_core_strategy_evidence import (
    _AllowWebPolicy,
    _CollectionStore,
    _Embedder,
    _LongTermMemory,
    _Provider,
    _SearchCapability,
    _session_factory,
)

pytestmark = pytest.mark.asyncio

_TENANT = TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise")


def _gateway(search_capability: Any = None) -> RetrievalGateway:
    return RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
            llm_resolver=lambda *_: ResolvedLLM(provider=_Provider(["unused"]), model="m"),
            search_capability=search_capability or _SearchCapability(),
            policy_services=(_AllowWebPolicy(),),
            long_term_memory=_LongTermMemory(),
        )
    )


async def test_precomputed_strategies_are_not_available_without_a_collection() -> None:
    statuses = await _gateway().readiness_all(_TENANT)
    for strategy in (RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING):
        assert statuses[strategy].available is False
        assert statuses[strategy].reason == "collection_index_required"
    assert statuses[RAGStrategy.HYBRID].available is True


async def test_precomputed_strategies_follow_the_collection_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import gateway as gateway_module
    from app.rag.catalogue import ReadinessFact

    seen: list[tuple[str, tuple[RAGStrategy, ...]]] = []

    async def probe(factory: Any, tenant_id: str, collection_id: str, strategies: Any) -> Any:
        seen.append((collection_id, tuple(strategies)))
        return {
            RAGStrategy.RAPTOR: ReadinessFact(True, "ready"),
            RAGStrategy.AGENTIC_CHUNKING: ReadinessFact(
                False, "requires agentic-chunking indexing"
            ),
        }

    monkeypatch.setattr(gateway_module, "_probe_precomputed_indexes", probe)
    statuses = await _gateway().readiness_all(_TENANT, collection_id="col-1")
    assert statuses[RAGStrategy.RAPTOR].available is True
    assert statuses[RAGStrategy.AGENTIC_CHUNKING].available is False
    assert statuses[RAGStrategy.AGENTIC_CHUNKING].reason == "requires agentic-chunking indexing"
    assert seen == [("col-1", (RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING))]


class _DownSearch(_SearchCapability):
    def __init__(self) -> None:
        self.probes: list[bool] = []

    async def health_reason(self, *, probe: bool = True) -> str | None:
        self.probes.append(probe)
        return "backend_outage"


async def test_web_augmented_is_unavailable_when_the_search_backend_is_down() -> None:
    search = _DownSearch()
    statuses = await _gateway(search).readiness_all(_TENANT)
    assert statuses[RAGStrategy.WEB_AUGMENTED].available is False
    assert statuses[RAGStrategy.WEB_AUGMENTED].reason == "web_search_backend_outage"
    assert search.probes == [True], "discovery may probe a stale health"


def _governed(handler: Any) -> tuple[GovernedWebSearchCapability, list[str]]:
    calls: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        result: httpx.Response = handler(request)
        return result

    backend = WebSearchTool(
        searxng_url="http://searxng.test",
        fallback_to_duckduckgo=False,
        transport=httpx.MockTransport(record),
    )
    return (
        GovernedWebSearchCapability(backend=backend, policy_services=(_AllowWebPolicy(),)),
        calls,
    )


async def test_health_probe_reports_an_unreachable_backend_and_is_cached() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    capability, calls = _governed(refuse)
    assert await capability.health_reason() == "backend_outage"
    assert await capability.health_reason() == "backend_outage"
    assert calls == ["/healthz"], "a fresh last-known health is reused, not re-probed"


async def test_health_probe_ok() -> None:
    capability, calls = _governed(lambda request: httpx.Response(200, text="OK"))
    assert await capability.health_reason() is None
    assert calls == ["/healthz"]


async def test_execution_path_never_probes_and_unknown_counts_as_healthy() -> None:
    capability, calls = _governed(lambda request: httpx.Response(200, text="OK"))
    assert await capability.health_reason(probe=False) is None
    assert calls == []


async def test_a_failed_search_becomes_the_last_known_health() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    capability, calls = _governed(refuse)
    with pytest.raises(WebSearchCapabilityError):
        await capability.search(
            WebSearchRequest(
                tenant_context=_TENANT,
                query="q",
                allowed_domains=(),
                max_results=3,
                max_bytes=10_000,
                timeout_seconds=2.0,
                report=WebSearchReport(),
            )
        )
    assert await capability.health_reason(probe=False) == "backend_outage"
    assert calls == ["/search"]


async def test_strategies_endpoint_evaluates_the_requested_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.api.rag_platform import list_strategies
    from app.rag import gateway as gateway_module
    from app.rag.catalogue import ReadinessFact

    async def probe(factory: Any, tenant_id: str, collection_id: str, strategies: Any) -> Any:
        assert collection_id == "col-1"
        return {strategy: ReadinessFact(True, "ready") for strategy in strategies}

    monkeypatch.setattr(gateway_module, "_probe_precomputed_indexes", probe)
    request = SimpleNamespace(
        state=SimpleNamespace(tenant=_TENANT),
        app=SimpleNamespace(state=SimpleNamespace(retrieval_gateway=_gateway())),
    )
    payload = await list_strategies(request, collection_id="col-1")  # type: ignore[arg-type]
    by_id = {item["id"]: item for item in payload["strategies"]}
    assert payload["collection_id"] == "col-1"
    assert by_id["raptor"]["available"] is True
    assert by_id["agentic_chunking"]["available"] is True
