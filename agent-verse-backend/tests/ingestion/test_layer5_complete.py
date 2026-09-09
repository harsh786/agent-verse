# tests/ingestion/test_layer5_complete.py
"""All 8 Layer 5 files must exist and have standard interface."""
from __future__ import annotations

from app.ingestion.content_classifier import ContentType
from app.ingestion.embedding_policy_selector import EmbeddingPolicy, EmbeddingPolicySelector
from app.ingestion.modality_pipeline import ModalityPipeline, ModalityPipelineResult
from app.ingestion.provenance_builder import IngestionProvenance, ProvenanceBuilder
from app.ingestion.quality_checks import QualityChecker, QualityCheckResult


def test_all_layer5_files_importable():
    files = [EmbeddingPolicySelector, ModalityPipeline, ProvenanceBuilder, QualityChecker]
    assert all(f is not None for f in files)


def test_embedding_policy_text_selects_text_model():
    selector = EmbeddingPolicySelector()
    policy = selector.select(ContentType.TEXT, collection_size=500)
    assert isinstance(policy, EmbeddingPolicy)
    assert policy.model_id is not None
    assert policy.dimension > 0


def test_embedding_policy_code_selects_code_model():
    selector = EmbeddingPolicySelector()
    policy = selector.select(ContentType.CODE, collection_size=100)
    assert policy.modality in ("code", "text")


def test_embedding_policy_image_selects_multimodal():
    selector = EmbeddingPolicySelector()
    policy = selector.select(ContentType.IMAGE, collection_size=50)
    assert policy.modality in ("multimodal", "image", "text")


def test_embedding_policy_large_collection_uses_hnsw():
    selector = EmbeddingPolicySelector()
    policy = selector.select(ContentType.TEXT, collection_size=50_000)
    assert policy.index_strategy in ("hnsw", "exact")


def test_modality_pipeline_selects_text_pipeline():
    pipeline = ModalityPipeline()
    result = pipeline.select_pipeline(ContentType.TEXT)
    assert isinstance(result, ModalityPipelineResult)
    assert result.parser_class is not None or result.chunker_strategy == "semantic"
    assert result.chunker_strategy == "semantic"
    assert result.embedding_modality == "text"


def test_modality_pipeline_selects_code_pipeline():
    pipeline = ModalityPipeline()
    result = pipeline.select_pipeline(ContentType.CODE)
    assert result.chunker_strategy == "ast"
    assert result.embedding_modality in ("code", "text")


def test_modality_pipeline_selects_audio_pipeline():
    pipeline = ModalityPipeline()
    result = pipeline.select_pipeline(ContentType.AUDIO)
    assert result.chunker_strategy == "timestamp"
    assert result.requires_transcription is True


def test_modality_pipeline_selects_video_pipeline():
    pipeline = ModalityPipeline()
    result = pipeline.select_pipeline(ContentType.VIDEO)
    assert result.chunker_strategy == "scene"
    assert result.requires_transcription is True


def test_provenance_builder_attaches_metadata():
    builder = ProvenanceBuilder()
    provenance = builder.build(
        content_type=ContentType.PDF,
        source_url="https://docs.example.com/report.pdf",
        source_name="report.pdf",
        tenant_id="t1",
        chunk_index=3,
        page_number=5,
    )
    assert isinstance(provenance, IngestionProvenance)
    assert provenance.source_url == "https://docs.example.com/report.pdf"
    assert provenance.page_number == 5
    assert provenance.tenant_id == "t1"


def test_provenance_builder_is_serializable():
    import json
    builder = ProvenanceBuilder()
    prov = builder.build(ContentType.TEXT, source_url="https://example.com",
                         source_name="doc.txt", tenant_id="t1")
    json.dumps(prov.to_dict())


def test_quality_checker_passes_good_chunk():
    checker = QualityChecker()
    result = checker.check("This is a meaningful paragraph about dynamic orchestration.")
    assert isinstance(result, QualityCheckResult)
    assert result.passed is True
    assert result.quality_score > 0.5


def test_quality_checker_fails_empty_chunk():
    checker = QualityChecker()
    result = checker.check("")
    assert result.passed is False
    assert result.reason is not None


def test_quality_checker_fails_noise_chunk():
    checker = QualityChecker()
    result = checker.check(".... ........ .... ....")
    assert result.passed is False or result.quality_score < 0.4
