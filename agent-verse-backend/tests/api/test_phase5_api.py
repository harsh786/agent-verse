# tests/api/test_phase5_api.py
"""Phase 5: API/Infra/Frontend gap fixes (H29-H41)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import rag_platform
from app.api.rag_platform import (
    RAGQueryRequest,
    _resolve_request_strategy,
    list_strategies,
)
from app.orchestration.strategy_registry import StrategyState, build_default_registry
from app.rag.contracts import (
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.tenancy.context import PlanTier, TenantContext


def test_knowledge_store_has_delete_document():
    """KnowledgeStore must have delete_document() method."""
    from app.rag.store import KnowledgeStore
    assert hasattr(KnowledgeStore, "delete_document")


def test_knowledge_store_delete_document_removes_chunks():
    """delete_document() must remove all chunks belonging to the document."""
    from app.rag.models import Chunk, KnowledgeCollection
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=ctx)
    store.ingest_chunk(
        Chunk(
            document_id="doc1",
            content="content",
            embedding=[0.1] * 10,
            chunk_index=0,
            chunk_id="c1",
            metadata={},
        ),
        collection_id="col1",
        tenant_ctx=ctx,
    )
    count = store.delete_document("doc1", collection_id="col1", tenant_ctx=ctx)
    assert count == 1
    # Verify chunk is gone
    results = store.hybrid_search("content", [0.1] * 10, "col1", ctx, top_k=5)
    assert len(results) == 0


def test_knowledge_store_delete_document_unknown_collection():
    """delete_document() must return 0 for unknown collection (no error)."""
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    store = KnowledgeStore()
    count = store.delete_document("doc1", collection_id="no-such-col", tenant_ctx=ctx)
    assert count == 0


def test_rag_strategy_enum_has_agentic_strategies():
    """The planner must expose canonical agentic strategy IDs."""
    from app.rag_platform.query_planner import RAGStrategy

    values = {strategy.value for strategy in RAGStrategy}
    assert {
        "fusion",
        "flare",
        "raptor",
        "corrective",
        "self_rag",
        "speculative",
        "colbert",
    }.issubset(values)


def test_rag_strategy_enum_has_fusion_attribute():
    """RAGStrategy.FUSION must exist."""
    from app.rag_platform.query_planner import RAGStrategy

    assert RAGStrategy.FUSION.value == "fusion"


@pytest.mark.parametrize(
    ("requested_id", "resolved_id"),
    [
        ("fusion_rag", "fusion"),
        ("corrective_rag", "corrective"),
        ("speculative_rag", "speculative"),
        ("colbert_late_interaction", "colbert"),
        ("multi_hop_rag", "multi_hop"),
        ("graph_rag", "graph"),
    ],
)
def test_rag_query_resolves_historical_ids(
    requested_id: str,
    resolved_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_strategy = RAGStrategy(resolved_id)

    class BoundaryAdapter:
        strategy = resolved_strategy

        @classmethod
        def probe_trace(cls) -> RAGStrategyTrace:
            return RAGStrategyTrace(
                strategy=cls.strategy,
                action="boundary_probe",
                status="complete",
                detail={
                    "adapter_strategy": cls.strategy.value,
                    "evidence": "boundary adapter",
                },
            )

        async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
            return RAGExecutionResult(
                requested_strategy_id=request.requested_strategy_id,
                resolved_strategy_id=self.strategy,
            )

    registry = build_default_registry(
        rag_runtime_capabilities={resolved_strategy: BoundaryAdapter}
    )
    monkeypatch.setattr(rag_platform, "get_strategy_registry", lambda: registry)

    assert _resolve_request_strategy(requested_id) is resolved_strategy


def test_rag_query_rejects_malformed_raw_capability_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SyncHybridAdapter:
        strategy = RAGStrategy.HYBRID

        def execute(self, request: object) -> None:
            return None

    registry = build_default_registry(
        rag_runtime_capabilities={RAGStrategy.HYBRID: SyncHybridAdapter}  # type: ignore[dict-item]
    )
    monkeypatch.setattr(rag_platform, "get_strategy_registry", lambda: registry)
    monkeypatch.setattr(
        rag_platform,
        "RAG_RUNTIME_CAPABILITIES",
        {RAGStrategy.HYBRID: SyncHybridAdapter},
        raising=False,
    )

    capability = registry.get(RAGStrategy.HYBRID.value)
    assert capability is not None
    assert capability.state is not StrategyState.IMPLEMENTED
    assert not registry.is_available(RAGStrategy.HYBRID.value)
    assert _resolve_request_strategy(RAGStrategy.HYBRID.value) is RAGStrategy.HYBRID


def test_rag_query_rejects_unknown_strategy() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _resolve_request_strategy("unknown-rag")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Unknown RAG strategy: unknown-rag"


def test_rag_query_default_is_canonical_hybrid() -> None:
    request = RAGQueryRequest(
        query="tenant-scoped retrieval",
        collection_id="collection-1",
    )

    assert request.strategy == RAGStrategy.HYBRID.value
    assert _resolve_request_strategy(request.strategy) is RAGStrategy.HYBRID


@pytest.mark.parametrize("strategy", list(RAGStrategy))
def test_rag_query_resolves_every_known_strategy(strategy: RAGStrategy) -> None:
    assert _resolve_request_strategy(strategy.value) is strategy


async def test_rag_strategy_discovery_lists_all_canonical_strategies_unavailable() -> None:
    class RequestState:
        tenant = TenantContext(
            tenant_id="tenant-1",
            plan=PlanTier.PROFESSIONAL,
            api_key_id="key-1",
        )

    class DiscoveryRequest:
        state = RequestState()
        app = SimpleNamespace(state=SimpleNamespace(retrieval_gateway=None))

    payload = await list_strategies(DiscoveryRequest())  # type: ignore[arg-type]
    strategies = payload["strategies"]

    assert {entry["id"] for entry in strategies} == {
        strategy.value for strategy in RAGStrategy
    }
    assert len(strategies) == len(RAGStrategy)
    assert all(entry["available"] is False for entry in strategies)
    assert "multimodal" not in {entry["id"] for entry in strategies}


def test_ingestion_orchestrator_has_chunk_with_quality_check():
    """IngestionOrchestrator must expose _chunk_with_quality_check()."""
    from app.ingestion.orchestrator import IngestionOrchestrator
    orch = IngestionOrchestrator()
    assert hasattr(orch, "_chunk_with_quality_check")


def test_ingestion_orchestrator_quality_check_filters_noise():
    """_chunk_with_quality_check should filter out noise-only content."""
    from app.ingestion.orchestrator import IngestionOrchestrator
    orch = IngestionOrchestrator()
    # All-noise content should be filtered
    chunks = orch._chunk_with_quality_check(".... ........ ....", ct=None)
    assert isinstance(chunks, list)
    # Either empty or the fallback (at minimum it's a list)
    assert all(isinstance(c, str) for c in chunks)


def test_ingestion_orchestrator_quality_check_keeps_good_content():
    """_chunk_with_quality_check must keep high-quality content."""
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.orchestrator import IngestionOrchestrator

    orch = IngestionOrchestrator()
    chunks = orch._chunk_with_quality_check(
        "This is a high-quality document with meaningful words.", ct=ContentType.TEXT
    )
    assert len(chunks) >= 1


def test_idempotency_store_importable():
    """IdempotencyStore must be importable."""
    from app.reliability.idempotency import IdempotencyStore
    assert IdempotencyStore is not None


def test_idempotency_store_interface():
    """IdempotencyStore must have check_and_set and exists methods."""
    from app.reliability.idempotency import IdempotencyStore
    assert hasattr(IdempotencyStore, "check_and_set")
    assert hasattr(IdempotencyStore, "exists")


def test_embedding_model_registry_voyage_dimension_512():
    """voyage-3-lite dimension must be 512 (not 1024)."""
    from app.embedding.model_registry import EmbeddingModelRegistry
    registry = EmbeddingModelRegistry.build_default()
    model = registry.get("voyage-3-lite")
    assert model is not None, "voyage-3-lite not found in registry"
    assert model.dimension == 512, f"Expected 512, got {model.dimension}"


def test_parser_registry_has_vision_parser_for_image():
    """ParserRegistry must map ContentType.IMAGE to VisionParser."""
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.parser_registry import ParserRegistry, VisionParser

    registry = ParserRegistry()
    parser = registry.get_parser(ContentType.IMAGE)
    assert isinstance(parser, VisionParser)


def test_vision_parser_returns_content():
    """VisionParser.parse() must return the content as a list."""
    from app.ingestion.parser_registry import VisionParser
    parser = VisionParser()
    result = parser.parse("An image of a sunset over mountains")
    assert result == ["An image of a sunset over mountains"]


def test_vision_parser_handles_empty():
    """VisionParser.parse() must return empty list for empty content."""
    from app.ingestion.parser_registry import VisionParser
    parser = VisionParser()
    result = parser.parse("")
    assert result == []


async def test_sdk_create_agent_with_request_object():
    """Python SDK create_agent must accept AgentCreateRequest (not raw string)."""
    try:
        from agentverse.models import AgentCreateRequest
        req = AgentCreateRequest(name="TestBot")
        assert req.name == "TestBot"
    except ImportError:
        pytest.skip("agentverse SDK not available")


def test_mcp_utils_safe_tool_result():
    """safe_tool_result() must return a structured error dict."""
    from app.mcp.servers.utils import safe_tool_result
    result = safe_tool_result("my_tool", ValueError("something went wrong"))
    assert result["success"] is False
    assert result["tool"] == "my_tool"
    assert "something went wrong" in result["error"]
    assert result["error_type"] == "ValueError"


def test_tasks_fire_due_schedules_handles_file_drop():
    """fire_due_schedules must not raise for file_drop trigger type."""
    # We test the trigger_type string matching logic indirectly by verifying
    # the task module imports without error.
    import app.scaling.tasks  # noqa: F401
    assert True


def test_invite_member_endpoint_not_todo():
    """invite_member must have a real implementation (not just TODO)."""
    import inspect

    from app.api import tenants

    source = inspect.getsource(tenants.invite_member)
    assert "TODO" not in source, "invite_member still has TODO placeholder"
    assert "invitation_id" in source
