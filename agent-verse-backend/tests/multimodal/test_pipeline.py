"""Tests for MultimodalPipeline (D-11 honest labeling, D-14 router wiring,
D-23 table/code modalities + planning-context injection helpers).
"""

from __future__ import annotations

import base64
from unittest.mock import Mock

from app.ai_router.model_orchestrator import ModelOrchestrator
from app.ingestion.content_classifier import ContentType
from app.multimodal.models import Modality
from app.multimodal.pipeline import MultimodalPipeline

_TINY_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00").decode()


class _VisionProvider:
    """Fake LLMProvider that supports vision and echoes the requested model."""

    def __init__(self) -> None:
        self.last_request = None

    def supports_vision(self) -> bool:
        return True

    async def complete(self, request):
        self.last_request = request
        from app.providers.base import CompletionResponse

        return CompletionResponse(content="A red bicycle leaning on a wall.", model=request.model)


# ── D-14: ModelOrchestrator.select_for_content_type is actually reachable ──


async def test_ingest_image_calls_model_orchestrator_select_for_content_type() -> None:
    """This is the 'is it wired' test: before D-14, select_for_content_type
    had zero non-test callers. It must now be invoked from the ingestion
    dispatch path for IMAGE."""
    orchestrator = ModelOrchestrator()
    orchestrator.select_for_content_type = Mock(  # type: ignore[method-assign]
        wraps=orchestrator.select_for_content_type
    )
    pipeline = MultimodalPipeline(model_orchestrator=orchestrator)
    pipeline.set_provider(_VisionProvider())

    await pipeline.ingest_image(_TINY_PNG_B64, "tenant-1")

    orchestrator.select_for_content_type.assert_called_once_with(ContentType.IMAGE)


async def test_ingest_audio_calls_model_orchestrator_select_for_content_type() -> None:
    orchestrator = ModelOrchestrator()
    orchestrator.select_for_content_type = Mock(  # type: ignore[method-assign]
        wraps=orchestrator.select_for_content_type
    )
    pipeline = MultimodalPipeline(model_orchestrator=orchestrator)

    await pipeline.ingest_audio(base64.b64encode(b"fake-audio").decode(), "tenant-1")

    orchestrator.select_for_content_type.assert_called_once_with(ContentType.AUDIO)


async def test_ingest_video_calls_model_orchestrator_select_for_content_type() -> None:
    orchestrator = ModelOrchestrator()
    orchestrator.select_for_content_type = Mock(  # type: ignore[method-assign]
        wraps=orchestrator.select_for_content_type
    )
    pipeline = MultimodalPipeline(model_orchestrator=orchestrator)

    await pipeline.ingest_video(base64.b64encode(b"fake-video").decode(), "tenant-1")

    orchestrator.select_for_content_type.assert_called_once_with(ContentType.VIDEO)


async def test_ingest_image_uses_router_selected_extractor_model() -> None:
    """The extractor model used in the real LLM call must come from the
    router, not a hardcoded literal."""
    orchestrator = ModelOrchestrator()
    provider = _VisionProvider()
    pipeline = MultimodalPipeline(model_orchestrator=orchestrator)
    pipeline.set_provider(provider)

    expected = orchestrator.select_for_content_type(ContentType.IMAGE)
    job = await pipeline.ingest_image(_TINY_PNG_B64, "tenant-1")

    assert provider.last_request is not None
    assert provider.last_request.model == expected.extractor_model
    assert job.metadata["extractor_model"] == expected.extractor_model
    assert job.metadata["extractor_modality"] == "image"


async def test_ingest_audio_records_router_selected_extractor_model_in_metadata() -> None:
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_audio(base64.b64encode(b"fake-audio").decode(), "tenant-1")
    assert job.metadata["extractor_model"]
    assert job.metadata["extractor_modality"] == "audio"


async def test_ingest_video_records_router_selected_extractor_model_in_metadata() -> None:
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_video(base64.b64encode(b"fake-video").decode(), "tenant-1")
    assert job.metadata["extractor_model"]
    assert job.metadata["extractor_modality"] == "video"


# ── D-11: honest embedding-strategy labeling ────────────────────────────────


async def test_text_ingestion_labels_direct_text_embed() -> None:
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_text("hello world", "tenant-1")
    assert job.metadata["embedding_strategy"] == "direct_text_embed"


async def test_image_ingestion_is_labeled_caption_then_text_embed_not_multimodal() -> None:
    pipeline = MultimodalPipeline()
    pipeline.set_provider(_VisionProvider())
    job = await pipeline.ingest_image(_TINY_PNG_B64, "tenant-1")
    assert job.status == "completed"
    assert job.metadata["embedding_strategy"] == "caption_then_text_embed"
    # The crux of D-11: nothing may claim a real multimodal (pixel-level)
    # embedding happened, because VoyageProvider.embed (and every other
    # configured embedder) is text-only.
    assert job.metadata["real_multimodal_embedding"] is False


async def test_audio_ingestion_with_transcript_is_labeled_transcript_then_text_embed() -> None:
    pipeline = MultimodalPipeline()

    async def _fake_transcribe(_audio_b64: str) -> str:
        return "hello from the recording"

    pipeline._transcribe_audio = _fake_transcribe  # type: ignore[method-assign]
    job = await pipeline.ingest_audio(base64.b64encode(b"fake-audio").decode(), "tenant-1")

    assert job.spans[0].content == "hello from the recording"
    assert job.metadata["embedding_strategy"] == "transcript_then_text_embed"
    assert job.metadata["real_multimodal_embedding"] is False


async def test_audio_ingestion_without_transcript_does_not_fabricate_a_span() -> None:
    """No Whisper key / failed transcription -> honest gap, not a fake string."""
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_audio(base64.b64encode(b"fake-audio").decode(), "tenant-1")
    assert job.spans == []
    assert job.metadata["audio_transcription"] == "unavailable"


async def test_video_visual_analysis_is_labeled_unavailable_not_fabricated() -> None:
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_video(base64.b64encode(b"fake-video").decode(), "tenant-1")
    assert job.metadata["video_visual_processing"] == "unavailable"
    visual_spans = [s for s in job.spans if s.modality == Modality.VIDEO]
    assert visual_spans and visual_spans[0].confidence == 0.0


# ── D-23a: table and code modalities ────────────────────────────────────────


async def test_ingest_code_chunks_by_symbol_boundary() -> None:
    pipeline = MultimodalPipeline()
    source = (
        "def add(a, b):\n    return a + b\n\n\nclass Greeter:\n    def hello(self):\n"
        "        return 'hi'\n"
    )
    job = await pipeline.ingest_code(source, "tenant-1", language="python")
    assert job.status == "completed"
    assert job.asset_type == Modality.CODE
    assert len(job.spans) >= 2
    assert all(span.modality == Modality.CODE for span in job.spans)
    assert all(span.language == "python" for span in job.spans)
    symbol_types = {span.metadata.get("symbol_type") for span in job.spans}
    assert "function" in symbol_types
    assert "class" in symbol_types


async def test_ingest_table_from_csv_string() -> None:
    pipeline = MultimodalPipeline()
    csv_text = "name,age\nAlice,30\nBob,25\n"
    job = await pipeline.ingest_table(csv_text, "tenant-1")
    assert job.status == "completed"
    assert job.asset_type == Modality.TABLE
    assert len(job.spans) == 1
    span = job.spans[0]
    assert span.metadata["columns"] == ["name", "age"]
    assert span.metadata["row_count"] == 2
    assert "Alice" in span.content and "Bob" in span.content


async def test_ingest_table_from_records() -> None:
    pipeline = MultimodalPipeline()
    records = [{"id": "1", "status": "ok"}, {"id": "2", "status": "fail"}]
    job = await pipeline.ingest_table(records, "tenant-1")
    span = job.spans[0]
    assert span.metadata["columns"] == ["id", "status"]
    assert span.metadata["row_count"] == 2


async def test_ingest_table_from_markdown_string() -> None:
    pipeline = MultimodalPipeline()
    md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    job = await pipeline.ingest_table(md, "tenant-1")
    span = job.spans[0]
    assert span.metadata["columns"] == ["a", "b"]
    assert span.metadata["row_count"] == 1


# ── D-23b: persistence via the injected job store ───────────────────────────


async def test_jobs_are_persisted_through_the_injected_job_store() -> None:
    from app.multimodal.job_store import AssetJobStore

    store = AssetJobStore()
    pipeline = MultimodalPipeline(job_store=store)
    job = await pipeline.ingest_text("hi", "tenant-1")

    # A brand new pipeline instance sharing the same store must see the job.
    other_pipeline = MultimodalPipeline(job_store=store)
    fetched = await other_pipeline.get_job(job.job_id, "tenant-1")
    assert fetched is not None
    assert fetched.job_id == job.job_id


async def test_set_job_store_swaps_persistence_backend() -> None:
    """Mirrors the two-phase wiring pattern: construct in-memory, then swap
    in a (Redis-backed in production) store once available."""
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_text("hi", "tenant-1")

    from app.multimodal.job_store import AssetJobStore

    new_store = AssetJobStore()
    pipeline.set_job_store(new_store)

    # The old job lived in the previous store, not the freshly-swapped one.
    assert await pipeline.get_job(job.job_id, "tenant-1") is None

    new_job = await pipeline.ingest_text("hi again", "tenant-1")
    assert await pipeline.get_job(new_job.job_id, "tenant-1") is not None


# ── Oversized attachment rejection ──────────────────────────────────────────
#
# Finding: before this, none of the binary ingest_* methods enforced any size
# limit on the incoming base64 payload -- an arbitrarily large attachment
# would be handed straight to a vision LLM / Whisper / ffmpeg. The analogous
# mission-attachment upload path (app/org/router.py) already rejects at 25 MB
# with a clear 413; MultimodalPipeline now mirrors that limit
# (_MAX_ATTACHMENT_BYTES) and fails the job cleanly instead. These tests
# pin down the fix and the underlying size-estimation helper.


def _b64_of_size(num_bytes: int) -> str:
    """Base64 string whose *decoded* size is exactly num_bytes."""
    return base64.b64encode(b"x" * num_bytes).decode()


class TestDecodedB64Size:
    def test_empty_string_is_zero(self) -> None:
        from app.multimodal.pipeline import _decoded_b64_size

        assert _decoded_b64_size("") == 0

    def test_matches_actual_decoded_length_no_padding(self) -> None:
        from app.multimodal.pipeline import _decoded_b64_size

        data = base64.b64encode(b"x" * 300).decode()  # 300 % 3 == 0 -> no padding
        assert _decoded_b64_size(data) == 300

    def test_matches_actual_decoded_length_with_padding(self) -> None:
        from app.multimodal.pipeline import _decoded_b64_size

        for n in (1, 2, 3, 10, 4096):
            data = base64.b64encode(b"y" * n).decode()
            assert _decoded_b64_size(data) == n, f"mismatch for n={n}"


async def test_ingest_image_over_limit_is_rejected_before_any_llm_call() -> None:
    from app.multimodal.pipeline import _MAX_ATTACHMENT_BYTES

    provider = _VisionProvider()
    pipeline = MultimodalPipeline()
    pipeline.set_provider(provider)
    oversized = _b64_of_size(_MAX_ATTACHMENT_BYTES + 1)

    job = await pipeline.ingest_image(oversized, "tenant-1")

    assert job.status == "failed"
    assert job.error is not None
    assert "25 MB" in job.error
    assert job.spans == []
    # No expensive vision-provider call was made for an attachment this large.
    assert provider.last_request is None


async def test_ingest_image_at_or_under_limit_is_not_rejected() -> None:
    from app.multimodal.pipeline import _MAX_ATTACHMENT_BYTES

    provider = _VisionProvider()
    pipeline = MultimodalPipeline()
    pipeline.set_provider(provider)
    just_under = _b64_of_size(min(_MAX_ATTACHMENT_BYTES, 1024))  # keep test fast

    job = await pipeline.ingest_image(just_under, "tenant-1")

    assert job.status == "completed"
    assert job.error is None


async def test_ingest_pdf_over_limit_fails_job_with_clear_error() -> None:
    from app.multimodal.pipeline import _MAX_ATTACHMENT_BYTES

    pipeline = MultimodalPipeline()
    oversized = _b64_of_size(_MAX_ATTACHMENT_BYTES + 1)

    job = await pipeline.ingest_pdf(oversized, "tenant-1")

    assert job.status == "failed"
    assert job.error is not None
    assert "25 MB" in job.error


async def test_ingest_audio_over_limit_is_rejected_before_transcription() -> None:
    from app.multimodal.pipeline import _MAX_ATTACHMENT_BYTES

    orchestrator = ModelOrchestrator()
    orchestrator.select_for_content_type = Mock(  # type: ignore[method-assign]
        wraps=orchestrator.select_for_content_type
    )
    pipeline = MultimodalPipeline(model_orchestrator=orchestrator)
    oversized = _b64_of_size(_MAX_ATTACHMENT_BYTES + 1)

    job = await pipeline.ingest_audio(oversized, "tenant-1")

    assert job.status == "failed"
    assert job.error is not None
    assert "25 MB" in job.error
    # Rejected before even selecting an extractor model for the (wasted) call.
    orchestrator.select_for_content_type.assert_not_called()


async def test_ingest_video_over_limit_fails_job_with_clear_error() -> None:
    from app.multimodal.pipeline import _MAX_ATTACHMENT_BYTES

    pipeline = MultimodalPipeline()
    oversized = _b64_of_size(_MAX_ATTACHMENT_BYTES + 1)

    job = await pipeline.ingest_video(oversized, "tenant-1")

    assert job.status == "failed"
    assert job.error is not None
    assert "25 MB" in job.error
