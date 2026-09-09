"""D-10 at the ingestion discard-site: the selected embedding model must route.

Previously ``IngestionOrchestrator.ingest`` selected an embedding model, wrote it
to ``metadata["embedding_model"]``, and then embedded with a single fixed
``self._embedder`` — discarding the selection. These tests pin that the physical
embedding now uses the *selected* model id.
"""
from __future__ import annotations

from app.ingestion.orchestrator import IngestionOrchestrator
from app.providers.base import EmbedRequest, EmbedResponse
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
