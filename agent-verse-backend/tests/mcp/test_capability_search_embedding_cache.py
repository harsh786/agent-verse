"""a02-F032-04 / F032-07: CapabilitySearch caches descriptor embeddings and meters them.

``_search_semantic`` embedded every tool descriptor on every query, with no
cache and no budget charge.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.embedding.metering import EmbeddingBudgetExceededError
from app.mcp.capability_search import CapabilitySearch, reset_embedding_cache
from app.providers.base import EmbedRequest, EmbedResponse
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-capcache", plan=PlanTier.PROFESSIONAL, api_key_id="k")
TOOLS = [
    {"name": "github_list_repos", "description": "List repositories", "server_id": "gh"},
    {"name": "jira_search_issues", "description": "Search issues", "server_id": "jr"},
]


class _Embedder:
    _embed_model_name = "test-embed-v1"

    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requests.append(list(request.texts))
        return EmbedResponse(
            embeddings=[[1.0, float(len(t) % 7), 0.5] for t in request.texts]
        )


class _Controller:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[dict[str, Any]] = []

    async def check_and_record(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return self.allow


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_embedding_cache()


def _embedded_texts(embedder: _Embedder) -> list[str]:
    return [t for batch in embedder.requests for t in batch]


@pytest.mark.asyncio
async def test_descriptors_are_embedded_once_across_searches() -> None:
    embedder = _Embedder()
    for query in ("repos", "issues", "repos"):
        await CapabilitySearch(tools=TOOLS, embedder=embedder).search(query, top_k=2)
    texts = _embedded_texts(embedder)
    assert texts.count("github_list_repos List repositories") == 1
    assert texts.count("jira_search_issues Search issues") == 1
    assert texts.count("repos") == 1  # a repeated query is cached too


@pytest.mark.asyncio
async def test_a_changed_descriptor_is_embedded_again() -> None:
    embedder = _Embedder()
    await CapabilitySearch(tools=TOOLS, embedder=embedder).search("repos")
    changed = [{**TOOLS[0], "description": "List all repositories"}, TOOLS[1]]
    await CapabilitySearch(tools=changed, embedder=embedder).search("repos")
    assert "github_list_repos List all repositories" in _embedded_texts(embedder)


@pytest.mark.asyncio
async def test_another_model_does_not_reuse_the_vectors() -> None:
    first, second = _Embedder(), _Embedder()
    second._embed_model_name = "test-embed-v2"
    await CapabilitySearch(tools=TOOLS, embedder=first).search("repos")
    await CapabilitySearch(tools=TOOLS, embedder=second).search("repos")
    assert len(_embedded_texts(second)) == 3


@pytest.mark.asyncio
async def test_metered_search_charges_only_new_embeddings() -> None:
    embedder, controller = _Embedder(), _Controller()
    search = CapabilitySearch(
        tools=TOOLS, embedder=embedder, cost_controller=controller, meter=True
    )
    await search.search("repos", tenant_ctx=CTX)
    charged = len(controller.calls)
    assert charged == 2  # one query batch + one descriptor batch
    await search.search("repos", tenant_ctx=CTX)
    assert len(controller.calls) == charged  # everything cached: nothing charged
    assert all(c["tenant_ctx"] is CTX for c in controller.calls)


@pytest.mark.asyncio
async def test_an_exhausted_budget_refuses_the_embedding() -> None:
    embedder = _Embedder()
    search = CapabilitySearch(
        tools=TOOLS, embedder=embedder, cost_controller=_Controller(allow=False), meter=True
    )
    with pytest.raises(EmbeddingBudgetExceededError):
        await search.search("repos", tenant_ctx=CTX)
    assert embedder.requests == []
