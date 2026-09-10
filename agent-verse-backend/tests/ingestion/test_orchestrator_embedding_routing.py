"""D-10 at the ingestion discard-site: the selected embedding model must route.

Previously ``IngestionOrchestrator.ingest`` selected an embedding model, wrote it
to ``metadata["embedding_model"]``, and then embedded with a single fixed
``self._embedder`` — discarding the selection. These tests pin that the physical
embedding now uses the *selected* model id.
"""
from __future__ import annotations

import json

from app.ingestion.orchestrator import IngestionOrchestrator
from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)
from app.rag.contracts import RAGStrategy
from app.rag.indexing import IndexingDependency, RAGIndexingConfig, RAGIndexRecord
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="ingest-routing", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


class RecordingEmbedder:
    """Records the model id requested for each embed call."""

    provider_name = "recording"

    def __init__(self) -> None:
        self.requested_models: list[str] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requested_models.append(request.model)
        return EmbedResponse(
            embeddings=[[0.5, 0.5, 0.5] for _ in request.texts],
            model=request.model,
        )


async def test_code_ingestion_routes_to_code_model() -> None:
    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="code", collection_id="col-code"),
        tenant_ctx=TENANT,
    )
    embedder = RecordingEmbedder()
    orch = IngestionOrchestrator(knowledge_store=store, embedder=embedder)

    code = "def calculate(x, y):\n    return x + y\n\nclass Calculator:\n    pass"
    result = await orch.ingest(
        content=code,
        content_type="code",
        collection_id="col-code",
        tenant_ctx=TENANT,
        in_memory_only=True,
    )

    assert result.chunks_prepared >= 1
    # The code model was actually requested on the physical embedder.
    assert embedder.requested_models, "embedder was never called"
    assert set(embedder.requested_models) == {"voyage-code-3"}


async def test_text_ingestion_does_not_use_code_model() -> None:
    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="text", collection_id="col-text"),
        tenant_ctx=TENANT,
    )
    embedder = RecordingEmbedder()
    orch = IngestionOrchestrator(knowledge_store=store, embedder=embedder)

    result = await orch.ingest(
        content="The AgentVerse platform routes goals through dynamic orchestration.",
        content_type="text",
        collection_id="col-text",
        tenant_ctx=TENANT,
        in_memory_only=True,
    )

    assert result.chunks_prepared >= 1
    assert embedder.requested_models
    assert "voyage-code-3" not in embedder.requested_models


async def test_code_ingestion_routes_to_resolved_provider() -> None:
    """Multi-model routing: with an embed_provider_resolver wired, the selected
    model is embedded on ITS OWN provider (voyage), not the default embedder."""
    from app.embedding.orchestrator import build_provider_resolver

    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="code", collection_id="col-resolver"),
        tenant_ctx=TENANT,
    )
    default = RecordingEmbedder()
    voyage_provider = RecordingEmbedder()
    resolver = build_provider_resolver({"voyage": voyage_provider})
    orch = IngestionOrchestrator(
        knowledge_store=store,
        embedder=default,
        embed_provider_resolver=resolver,
    )

    await orch.ingest(
        content="def f(x):\n    return x * 2\n\nclass C:\n    pass",
        content_type="code",
        collection_id="col-resolver",
        tenant_ctx=TENANT,
        in_memory_only=True,
    )

    # The resolver-provided provider embedded the code, with the code model id.
    assert voyage_provider.requested_models == ["voyage-code-3"]
    # The default embedder was NOT used (routing actually happened).
    assert default.requested_models == []


async def test_ingestion_falls_back_to_default_when_provider_unresolved() -> None:
    """When the resolver has no entry for the selected provider, embedding falls
    back to the default embedder (single-provider deployments keep working)."""
    from app.embedding.orchestrator import build_provider_resolver

    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="code", collection_id="col-fallback"),
        tenant_ctx=TENANT,
    )
    default = RecordingEmbedder()
    # Resolver only knows an unrelated provider — the selected "voyage" is absent.
    resolver = build_provider_resolver({"openai": RecordingEmbedder()})
    orch = IngestionOrchestrator(
        knowledge_store=store,
        embedder=default,
        embed_provider_resolver=resolver,
    )

    await orch.ingest(
        content="def g():\n    return 1",
        content_type="code",
        collection_id="col-fallback",
        tenant_ctx=TENANT,
        in_memory_only=True,
    )

    assert default.requested_models == ["voyage-code-3"]


# ── D-10 on the *indexed* (RAGIndexingPipeline) path ──────────────────────────
#
# The plain path above already routes through the selection. Indexed ingestion
# (``rag_indexing_config.strategies`` set) instead built a ``RAGIndexingPipeline``
# bound to the single fixed ``self._embedder`` and never threaded a model id into
# its physical embed call at all — the selection computed just above it in
# ``ingest()`` was written to ``metadata["embedding_model"]`` and then discarded.
# These tests pin the fix at that second discard-site.


class RecordingCompletionProvider:
    """Minimal AGENTIC_CHUNKING completion stub: turns each chunk into one proposition."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        prompt = str(request.messages[-1].content)
        payload = json.loads(prompt.split("\n", maxsplit=1)[1])
        content = json.dumps([[f"{chunk} noted."] for chunk in payload])
        return CompletionResponse(content=content, model=request.model)


class RecordingIndexStore:
    """Fake store exercising the ``RAGIndexingPipeline`` persistence contract.

    ``_db`` is a non-None sentinel so ``IngestionOrchestrator.ingest`` treats this
    as a persisted (not in-memory-only) store, matching the indexed-ingestion
    precondition. ``get_collection_embedding_dim`` mirrors the real
    ``KnowledgeStore`` helper the orchestrator consults for dimension safety.
    """

    def __init__(self, *, established_dim: int | None = None) -> None:
        self._db = object()
        self.records: list[RAGIndexRecord] = []
        self._established_dim = established_dim

    async def persist_index_records(
        self,
        records: list[RAGIndexRecord],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        del collection_id, tenant_ctx
        self.records.extend(records)
        return [record.chunk_id for record in records]

    async def get_collection_embedding_dim(
        self, collection_id: str, *, tenant_ctx: TenantContext
    ) -> int | None:
        del collection_id, tenant_ctx
        return self._established_dim


def _agentic_config() -> RAGIndexingConfig:
    return RAGIndexingConfig(
        strategies=frozenset({RAGStrategy.AGENTIC_CHUNKING}),
        parent_window_size=1,
    )


def _agentic_dependencies() -> dict[RAGStrategy, IndexingDependency]:
    return {
        RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
            RecordingCompletionProvider(), "indexing-model"
        )
    }


async def test_indexed_ingestion_routes_to_selected_models_resolved_provider() -> None:
    """A code doc's selection (voyage-code-3) must reach ITS provider, not the default."""
    from app.embedding.orchestrator import build_provider_resolver

    store = RecordingIndexStore()
    default = RecordingEmbedder()
    voyage_provider = RecordingEmbedder()
    resolver = build_provider_resolver({"voyage": voyage_provider})
    orch = IngestionOrchestrator(
        knowledge_store=store,
        embedder=default,
        embed_provider_resolver=resolver,
        indexing_dependencies=_agentic_dependencies(),
        rag_indexing_config=_agentic_config(),
    )

    result = await orch.ingest(
        content="def calculate(x, y):\n    return x + y\n\nclass Calculator:\n    pass",
        content_type="code",
        collection_id="col-indexed-code",
        tenant_ctx=TENANT,
        source_identity="doc-indexed-code",
    )

    assert result.persisted
    assert store.records
    # The selected model's own provider did the physical embedding...
    assert voyage_provider.requested_models
    assert set(voyage_provider.requested_models) == {"voyage-code-3"}
    # ...and the default embedder was never called (real routing, not cosmetic).
    assert default.requested_models == []
    assert store.records[0].metadata["embedding_model_effective"] == "voyage-code-3"


async def test_indexed_ingestion_falls_back_to_default_when_provider_unresolved() -> None:
    """Selection present, but its provider isn't configured → default embedder,
    with the effective model honestly recorded in metadata."""
    from app.embedding.orchestrator import build_provider_resolver

    store = RecordingIndexStore()
    default = RecordingEmbedder()
    # Resolver only knows an unrelated provider — "voyage" (the selection) is absent.
    resolver = build_provider_resolver({"openai": RecordingEmbedder()})
    orch = IngestionOrchestrator(
        knowledge_store=store,
        embedder=default,
        embed_provider_resolver=resolver,
        indexing_dependencies=_agentic_dependencies(),
        rag_indexing_config=_agentic_config(),
    )

    result = await orch.ingest(
        content="def g():\n    return 1\n\nclass H:\n    pass",
        content_type="code",
        collection_id="col-indexed-fallback",
        tenant_ctx=TENANT,
        source_identity="doc-indexed-fallback",
    )

    assert result.persisted
    assert store.records
    assert default.requested_models
    assert set(default.requested_models) == {"voyage-code-3"}
    assert store.records[0].metadata["embedding_model_effective"] == "voyage-code-3"


async def test_indexed_ingestion_falls_back_to_default_on_dimension_mismatch() -> None:
    """Selected model's dimension (1024 for voyage-code-3) differs from the
    collection's established dimension → never write the mismatched vector;
    embed with the default (whose dimension already matches) instead."""
    from app.embedding.orchestrator import build_provider_resolver

    store = RecordingIndexStore(established_dim=999)
    default = RecordingEmbedder()
    voyage_provider = RecordingEmbedder()
    resolver = build_provider_resolver({"voyage": voyage_provider})
    orch = IngestionOrchestrator(
        knowledge_store=store,
        embedder=default,
        embed_provider_resolver=resolver,
        indexing_dependencies=_agentic_dependencies(),
        rag_indexing_config=_agentic_config(),
    )

    result = await orch.ingest(
        content="def h():\n    return 2\n\nclass I:\n    pass",
        content_type="code",
        collection_id="col-indexed-mismatch",
        tenant_ctx=TENANT,
        source_identity="doc-indexed-mismatch",
    )

    assert result.persisted
    assert store.records
    # The mismatched-dimension provider must NEVER be asked to embed.
    assert voyage_provider.requested_models == []
    # The default embedder was used, with no model override (its own safe default).
    assert default.requested_models
    assert all(model == "" for model in default.requested_models)
    # Never silently mislabelled: metadata says "default", not the selected model.
    assert store.records[0].metadata["embedding_model_effective"] == "default"
    assert store.records[0].metadata["embedding_model"] == "voyage-code-3"
