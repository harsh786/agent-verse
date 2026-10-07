"""Tests for previously untested modules:
  - app/multimodal/models.py
  - app/multimodal/pipeline.py  (public surface)
  - app/bootstrap/services.py   (public surface)
"""
from __future__ import annotations

import pytest

# ─────────────────────────────────────────────────────────────────────────────
#  multimodal.models
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalModels:
    def test_modality_enum_values(self) -> None:
        from app.multimodal.models import Modality
        assert Modality.TEXT == "text"
        assert Modality.IMAGE == "image"
        assert Modality.PDF == "pdf"
        assert Modality.AUDIO == "audio"
        assert Modality.VIDEO == "video"
        assert Modality.OCR == "ocr"

    def test_extracted_span_defaults(self) -> None:
        from app.multimodal.models import ExtractedSpan, Modality
        span = ExtractedSpan(content="Hello world", modality=Modality.TEXT)
        assert span.confidence == 1.0
        assert span.source_page is None
        assert span.bounding_box is None
        assert span.metadata == {}
        assert span.language is None

    def test_extracted_span_with_bounding_box(self) -> None:
        from app.multimodal.models import ExtractedSpan, Modality
        span = ExtractedSpan(
            content="Invoice #1234",
            modality=Modality.OCR,
            source_page=1,
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.05},
            confidence=0.95,
            language="en",
        )
        assert span.source_page == 1
        assert span.bounding_box["x"] == 0.1
        assert span.confidence == 0.95

    def test_extracted_span_audio(self) -> None:
        from app.multimodal.models import ExtractedSpan, Modality
        span = ExtractedSpan(
            content="Meeting started at 9am",
            modality=Modality.AUDIO,
            timestamp_start=0.0,
            timestamp_end=2.5,
        )
        assert span.timestamp_start == 0.0
        assert span.timestamp_end == 2.5

    def test_asset_ingestion_job_defaults(self) -> None:
        from app.multimodal.models import AssetIngestionJob, Modality
        job = AssetIngestionJob(
            job_id="j1", tenant_id="t1", asset_type=Modality.PDF,
        )
        assert job.status == "pending"
        assert job.spans == []
        assert job.error is None
        assert job.source_uri is None
        assert job.metadata == {}

    def test_asset_ingestion_job_with_uri(self) -> None:
        from app.multimodal.models import AssetIngestionJob, Modality
        job = AssetIngestionJob(
            job_id="j2",
            tenant_id="t2",
            asset_type=Modality.IMAGE,
            source_uri="s3://bucket/image.png",
            collection_id="col-abc",
            filename="image.png",
        )
        assert job.source_uri == "s3://bucket/image.png"
        assert job.collection_id == "col-abc"
        assert job.filename == "image.png"

    def test_asset_ingestion_job_status_transitions(self) -> None:
        from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality
        job = AssetIngestionJob(
            job_id="j3", tenant_id="t3", asset_type=Modality.TEXT,
        )
        job.status = "processing"
        assert job.status == "processing"

        span = ExtractedSpan(content="Extracted text", modality=Modality.TEXT)
        job.spans.append(span)
        job.status = "completed"
        assert len(job.spans) == 1
        assert job.status == "completed"


# ─────────────────────────────────────────────────────────────────────────────
#  multimodal.pipeline (public surface)
# ─────────────────────────────────────────────────────────────────────────────

class TestMultimodalPipeline:
    @pytest.fixture
    def pipeline(self):
        from app.multimodal.pipeline import MultimodalPipeline
        return MultimodalPipeline()

    def test_pipeline_instantiates(self, pipeline) -> None:
        assert pipeline is not None

    async def test_get_nonexistent_job_returns_none(self, pipeline) -> None:
        result = await pipeline.get_job("nonexistent-job", "tenant-x")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_job_wrong_tenant_returns_none(self, pipeline) -> None:
        """get_job must enforce tenant isolation."""
        # Create and store a job for tenant-a via ingest
        job = await pipeline.ingest_text(tenant_id="tenant-a", content="secret")
        # Try to retrieve it as tenant-b
        result = await pipeline.get_job(job.job_id, "tenant-b")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_job_correct_tenant_returns_job(self, pipeline) -> None:
        job = await pipeline.ingest_text(tenant_id="tenant-c", content="my data")
        result = await pipeline.get_job(job.job_id, "tenant-c")
        assert result is not None
        assert result.job_id == job.job_id

    @pytest.mark.asyncio
    async def test_ingest_text_returns_job(self, pipeline) -> None:
        job = await pipeline.ingest_text(
            tenant_id="tenant-d",
            content="Sample text for ingestion",
        )
        assert job is not None
        assert job.tenant_id == "tenant-d"
        assert job.status in ("completed", "failed", "processing")

    @pytest.mark.asyncio
    async def test_ingest_text_empty_string(self, pipeline) -> None:
        """Empty text should still create a job (returns empty spans)."""
        job = await pipeline.ingest_text(
            tenant_id="tenant-e",
            content="",
        )
        assert job is not None

    @pytest.mark.asyncio
    async def test_ingest_image_no_provider_falls_back(self, pipeline) -> None:
        """Without a vision provider the job should fail gracefully."""
        import base64
        tiny_png = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00").decode()
        job = await pipeline.ingest_image(
            tenant_id="tenant-f",
            image_base64=tiny_png,
        )
        assert job is not None
        # No provider → failed or completed with empty spans
        assert job.status in ("failed", "completed")

    def test_set_provider(self, pipeline) -> None:
        """set_provider should not raise."""
        from unittest.mock import MagicMock
        pipeline.set_provider(MagicMock())
        assert True  # no exception


# ─────────────────────────────────────────────────────────────────────────────
#  bootstrap.services (public surface)
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapServices:
    def test_build_services_importable(self) -> None:
        from app.bootstrap.services import build_services
        assert callable(build_services)

    def test_build_services_signature(self) -> None:
        """build_services must accept app and settings parameters."""
        import inspect

        from app.bootstrap.services import build_services
        sig = inspect.signature(build_services)
        params = list(sig.parameters.keys())
        assert "app" in params
        assert "settings" in params

    def test_bootstrap_routers_importable(self) -> None:
        from app.bootstrap import routers
        assert routers is not None
