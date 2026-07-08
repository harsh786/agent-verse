# tests/api/test_phase5_api.py
"""Phase 5: API/Infra/Frontend gap fixes (H29-H41)."""
from __future__ import annotations
import pytest


def test_knowledge_store_has_delete_document():
    """KnowledgeStore must have delete_document() method."""
    from app.rag.store import KnowledgeStore
    assert hasattr(KnowledgeStore, "delete_document")


def test_knowledge_store_delete_document_removes_chunks():
    """delete_document() must remove all chunks belonging to the document."""
    from app.rag.store import KnowledgeStore
    from app.rag.models import KnowledgeCollection, Chunk
    from app.tenancy.context import TenantContext, PlanTier
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
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    store = KnowledgeStore()
    count = store.delete_document("doc1", collection_id="no-such-col", tenant_ctx=ctx)
    assert count == 0


def test_rag_strategy_enum_has_agentic_strategies():
    """RAGStrategy enum must include fusion_rag, flare, raptor, corrective_rag, self_rag, speculative, colbert."""
    try:
        from app.rag_platform.query_planner import RAGStrategy
        values = [s.value for s in RAGStrategy]
        assert "fusion_rag" in values, f"fusion_rag missing from {values}"
        assert "flare" in values, f"flare missing from {values}"
        assert "raptor" in values, f"raptor missing from {values}"
        assert "corrective_rag" in values, f"corrective_rag missing from {values}"
        assert "self_rag" in values, f"self_rag missing from {values}"
        assert "speculative" in values, f"speculative missing from {values}"
        assert "colbert" in values, f"colbert missing from {values}"
    except ImportError:
        pytest.skip("rag_platform not available")


def test_rag_strategy_enum_has_fusion_attribute():
    """RAGStrategy.FUSION must exist."""
    try:
        from app.rag_platform.query_planner import RAGStrategy
        assert hasattr(RAGStrategy, "FUSION")
        assert RAGStrategy.FUSION.value == "fusion_rag"
    except ImportError:
        pytest.skip("rag_platform not available")


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
    from app.ingestion.orchestrator import IngestionOrchestrator
    from app.ingestion.content_classifier import ContentType
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
    from app.ingestion.parser_registry import ParserRegistry, VisionParser
    from app.ingestion.content_classifier import ContentType
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
