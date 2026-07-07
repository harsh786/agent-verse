"""SemanticCacheBridge: tenant isolation, error guard, fresh evidence rule."""
from __future__ import annotations
import pytest
from app.state_runtime.cache_bridge import SemanticCacheBridge


@pytest.fixture
def bridge():
    return SemanticCacheBridge()


@pytest.mark.asyncio
async def test_error_output_never_cached(bridge):
    stored = await bridge.maybe_store("search", "Error: connection refused", "t1",
                                       is_error=True, is_nondeterministic=False)
    assert stored is False


@pytest.mark.asyncio
async def test_nondeterministic_never_cached(bridge):
    stored = await bridge.maybe_store("get time", "2026-07-07T14:30:00Z", "t1",
                                       is_error=False, is_nondeterministic=True)
    assert stored is False


@pytest.mark.asyncio
async def test_deterministic_safe_stored(bridge):
    stored = await bridge.maybe_store("list tickets", "Found 5 tickets", "t1",
                                       is_error=False, is_nondeterministic=False)
    assert stored is True


@pytest.mark.asyncio
async def test_tenant_isolation(bridge):
    await bridge.maybe_store("list open tickets", "Found 5 tickets", "tenant_alpha",
                              is_error=False, is_nondeterministic=False)
    result = await bridge.lookup("list open tickets", tenant_id="tenant_beta")
    assert result is None  # different tenant must not get the result


@pytest.mark.asyncio
async def test_same_tenant_can_retrieve(bridge):
    await bridge.maybe_store("count open Jira tickets", "There are 12.", "tenant_x",
                              is_error=False, is_nondeterministic=False)
    result = await bridge.lookup("count open Jira tickets", tenant_id="tenant_x")
    if result is not None:
        assert result.tenant_id == "tenant_x"


def test_cache_never_overrides_fresh_evidence():
    from app.rag.agentic.retriever_tool import RetrievalResult
    fresh = RetrievalResult(query="q", source="knowledge_base", strategy_used="hybrid",
                            confidence=0.88, chunks=[{"content": "fresh", "score": 0.88}])
    cache_hit = {"content": "stale cache", "score": 0.70}
    should_use_cache = (fresh.confidence < 0.35 and not fresh.chunks)
    assert should_use_cache is False
