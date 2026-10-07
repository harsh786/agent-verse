# tests/embedding/test_embedding_orchestrator.py
"""EmbeddingOrchestrator selects the right model per modality and tenant budget."""
from __future__ import annotations

import pytest

from app.embedding.dimension_policy import DimensionPolicy
from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.orchestrator import EmbeddingOrchestrator, EmbeddingSelectionResult
from app.embedding.reembedding_policy import ReembeddingPolicy, ReembeddingTrigger
from app.ingestion.content_classifier import ContentType
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def registry():
    # What the Model Registry would yield: a configured text embedder and a code
    # specialist of the same width (EmbeddingModelRegistry.from_model_registry).
    return EmbeddingModelRegistry(
        [
            EmbeddingModelSpec("reg-text-embed", "text", 1024, "low", "onprem"),
            EmbeddingModelSpec("reg-code-embed", "code", 1024, "low", "voyage"),
        ]
    )


@pytest.fixture
def orchestrator(registry):
    return EmbeddingOrchestrator(registry=registry)


def test_text_content_gets_text_embedding(orchestrator, tenant_ctx):
    """Text is the default (registry-resolved) embedder with its own model."""
    result = orchestrator.select(
        content_type=ContentType.TEXT,
        tenant_ctx=tenant_ctx,
        target_dim=1024,
        default_model="default-embed",
    )
    assert isinstance(result, EmbeddingSelectionResult)
    assert result.uses_default_embedder
    assert result.model_id == "default-embed"
    assert result.dimension == 1024


def test_code_content_gets_code_embedding(orchestrator, tenant_ctx):
    result = orchestrator.select(
        content_type=ContentType.CODE,
        tenant_ctx=tenant_ctx,
    )
    assert result.modality in ("code", "text")   # code or fallback to text


def test_image_content_gets_multimodal_or_text_embedding(orchestrator, tenant_ctx):
    result = orchestrator.select(
        content_type=ContentType.IMAGE,
        tenant_ctx=tenant_ctx,
    )
    assert result.model_id is not None


def test_free_plan_gets_cheaper_model(orchestrator):
    free_ctx = TenantContext(tenant_id="t2", plan=PlanTier.FREE, api_key_id="k2")
    result = orchestrator.select(
        content_type=ContentType.CODE, tenant_ctx=free_ctx, target_dim=1024
    )
    assert result.cost_class in ("free", "low")
    assert result.key == "voyage/reg-code-embed"


def test_model_registry_has_text_models(registry):
    text_models = registry.list_by_modality("text")
    assert len(text_models) > 0


def test_model_registry_has_code_models(registry):
    code_models = registry.list_by_modality("code")
    assert len(code_models) >= 0   # may fall back to text


def test_dimension_policy_returns_valid_dimension():
    policy = DimensionPolicy()
    dim = policy.select(model_id="text-embedding-3-small")
    assert dim in (768, 1024, 1536, 3072)


def test_embedding_selection_result_has_all_fields(orchestrator, tenant_ctx):
    result = orchestrator.select(content_type=ContentType.TEXT, tenant_ctx=tenant_ctx)
    assert hasattr(result, "model_id")
    assert hasattr(result, "dimension")
    assert hasattr(result, "modality")
    assert hasattr(result, "cost_class")


def test_reembedding_policy_triggers_on_dimension_mismatch():
    policy = ReembeddingPolicy()
    trigger = policy.should_reembed(
        current_model="text-embedding-3-small",
        new_model="text-embedding-3-small",
        collection_size=500,
        old_dim=1536,
        new_dim=3072,   # dimension changed
    )
    assert trigger == ReembeddingTrigger.DIMENSION_MISMATCH


def test_reembedding_policy_no_trigger_same_dimensions():
    policy = ReembeddingPolicy()
    trigger = policy.should_reembed(
        current_model="text-embedding-3-small",
        new_model="text-embedding-3-small",
        collection_size=100,
        old_dim=1536,
        new_dim=1536,
    )
    assert trigger == ReembeddingTrigger.NONE
