"""Workflow LLM/RAG step: the query is embedded in the COLLECTION's vector space.

It used to pass ``embedder=provider`` — the CHAT provider — to
``KnowledgeStore.retrieve``, so an unbound collection's query was embedded with
another model than its documents (similarity scores were noise). The query is
now embedded with the collection's bound embedder, and an unbound collection
with the Model Registry embedder (the injected ``embedder`` service, else the
process embedder). The chat provider never embeds.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.providers.base import CompletionResponse, EmbedRequest, EmbedResponse
from app.providers.embedder_factory import (
    embedder_model_name,
    reset_process_embedder,
    set_process_embedder,
)
from app.workflow.context import ContextResolver
from app.workflow.dsl import RAGConfig, StepDefinition

TENANT = "tenant-wf-rag-embed"


class _Embedder:
    """An embedder of one model: records every query it embeds."""

    def __init__(self, model: str, dim: int = 3) -> None:
        self._embed_model_name = model
        self.embedding_dim = dim
        self.texts: list[str] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.texts.extend(request.texts)
        return EmbedResponse(
            embeddings=[[0.1] * self.embedding_dim for _ in request.texts],
            model=self._embed_model_name,
        )


class _ChatProvider(_Embedder):
    """The run's chat provider. It CAN embed (its own model) — and must not."""

    def __init__(self) -> None:
        super().__init__("chat-model-embeddings")

    async def complete(self, request: Any) -> CompletionResponse:
        return CompletionResponse(content='{"ok": true}', model="chat-model",
                                  input_tokens=1, output_tokens=1)


class _RecordingStore:
    """Captures the default embedder the step hands to ``retrieve``."""

    def __init__(self) -> None:
        self.embedders: list[Any] = []

    async def retrieve(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.embedders.append(kwargs.get("embedder"))
        return [{"content": "context"}]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.providers import guarded_completion

    async def _no_charge(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(guarded_completion, "_charge", _no_charge)
    reset_process_embedder()
    yield
    reset_process_embedder()


def _state() -> dict[str, Any]:
    return {
        "run_id": "run-rag",
        "tenant_id": TENANT,
        "inputs": {"text": "what is the refund policy"},
        "step_outputs": {},
        "vars": {},
        "is_test_run": False,
    }


def _node(**services: Any) -> Any:
    from app.workflow.steps.llm_step import LLMStepNode

    return LLMStepNode(
        StepDefinition(
            id="r1",
            type="llm",
            prompt="{{inputs.text}}",
            rag=RAGConfig(collection="kb", top_k=3),
        ),
        ContextResolver(),
        **services,
    )


async def test_rag_query_default_is_the_injected_registry_embedder_never_the_chat_provider(
) -> None:
    chat = _ChatProvider()
    registry_embedder = _Embedder("registry-embed")
    store = _RecordingStore()
    await _node(llm_provider=chat, knowledge_store=store, embedder=registry_embedder).execute(
        _state()
    )
    (default,) = store.embedders
    assert default is registry_embedder
    assert embedder_model_name(default) == "registry-embed"
    assert default is not chat


async def test_rag_query_default_falls_back_to_the_process_registry_embedder() -> None:
    """No ``embedder`` service (the worker's compiler): the process's Model
    Registry embedder — never the chat provider."""
    chat = _ChatProvider()
    registry_embedder = _Embedder("registry-embed")
    set_process_embedder(lambda: registry_embedder)
    store = _RecordingStore()
    await _node(llm_provider=chat, knowledge_store=store).execute(_state())
    (default,) = store.embedders
    assert embedder_model_name(default) == "registry-embed"
    assert default is not chat


async def test_rag_query_is_embedded_with_the_collections_bound_model() -> None:
    """A real KnowledgeStore: a collection bound to ``bound-embed`` is queried
    with THAT model's vectors; neither the registry default nor the chat
    provider embeds the query."""
    from app.rag.collection_embedders import CollectionEmbedders
    from app.rag.models import KnowledgeCollection
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    chat = _ChatProvider()
    registry_embedder = _Embedder("registry-embed")
    bound_embedder = _Embedder("bound-embed")

    class _Embedders(CollectionEmbedders):
        def _registry_entry(self, binding: Any) -> Any:
            return binding if binding.model == "bound-embed" else None

        def _cached(self, binding: Any, entry: Any) -> Any:
            return bound_embedder

    store = KnowledgeStore()
    store.collection_embedders = _Embedders(lambda: registry_embedder)
    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k")
    store.create_collection(
        KnowledgeCollection(
            name="kb",
            collection_id="col-bound",
            embedding_provider="onprem",
            embedding_model="bound-embed",
            embedding_dim=3,
        ),
        tenant_ctx=ctx,
    )

    await _node(llm_provider=chat, knowledge_store=store, embedder=registry_embedder).execute(
        _state()
    )

    assert bound_embedder.texts == ["what is the refund policy"]
    assert registry_embedder.texts == []
    assert chat.texts == []


async def test_unbound_collection_query_uses_the_registry_embedder() -> None:
    from app.rag.collection_embedders import CollectionEmbedders
    from app.rag.models import KnowledgeCollection
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    chat = _ChatProvider()
    registry_embedder = _Embedder("registry-embed")
    store = KnowledgeStore()
    store.collection_embedders = CollectionEmbedders(lambda: None)
    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k")
    store.create_collection(KnowledgeCollection(name="kb", collection_id="col-free"), tenant_ctx=ctx)

    await _node(llm_provider=chat, knowledge_store=store, embedder=registry_embedder).execute(
        _state()
    )

    assert registry_embedder.texts == ["what is the refund policy"]
    assert chat.texts == []
