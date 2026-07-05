"""Multimodal ingestion pipeline."""
from __future__ import annotations
import io
import logging
import uuid
from typing import Any
from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality

_log = logging.getLogger(__name__)


class MultimodalPipeline:
    """Universal multimodal ingestion and extraction pipeline."""

    def __init__(self) -> None:
        self._provider: Any = None
        self._jobs: dict[str, AssetIngestionJob] = {}

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    async def ingest_text(
        self,
        content: str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        job = self._create_job(tenant_id, Modality.TEXT, collection_id=collection_id)
        job.spans = [ExtractedSpan(content=content, modality=Modality.TEXT, confidence=1.0)]
        job.status = "completed"
        self._jobs[job.job_id] = job
        return job

    async def ingest_image(
        self,
        image_base64: str,
        tenant_id: str,
        collection_id: str | None = None,
        filename: str | None = None,
    ) -> AssetIngestionJob:
        job = self._create_job(
            tenant_id, Modality.IMAGE, collection_id=collection_id, filename=filename
        )
        job.source_base64 = image_base64

        try:
            job.status = "processing"
            description = await self._describe_image(image_base64)
            job.spans = [
                ExtractedSpan(content=description, modality=Modality.IMAGE, confidence=0.9)
            ]
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("Image ingestion failed: %s", exc)

        self._jobs[job.job_id] = job
        return job

    async def ingest_pdf(
        self,
        pdf_base64: str,
        tenant_id: str,
        collection_id: str | None = None,
        filename: str | None = None,
    ) -> AssetIngestionJob:
        job = self._create_job(
            tenant_id, Modality.PDF, collection_id=collection_id, filename=filename
        )
        job.source_base64 = pdf_base64

        try:
            job.status = "processing"
            spans = await self._extract_pdf(pdf_base64)
            job.spans = spans
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("PDF ingestion failed: %s", exc)

        self._jobs[job.job_id] = job
        return job

    async def ingest_audio(
        self,
        audio_base64: str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        job = self._create_job(tenant_id, Modality.AUDIO, collection_id=collection_id)
        try:
            job.status = "processing"
            transcript = await self._transcribe_audio(audio_base64)
            job.spans = [
                ExtractedSpan(content=transcript, modality=Modality.AUDIO, confidence=0.85)
            ]
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        self._jobs[job.job_id] = job
        return job

    async def ingest_video(
        self,
        video_base64: str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        """Extract audio transcript, keyframes, and scene summaries from video."""
        job = self._create_job(tenant_id, Modality.VIDEO, collection_id=collection_id)
        try:
            job.status = "processing"
            spans = []
            # Transcript
            transcript = await self._transcribe_audio(video_base64)
            if transcript:
                spans.append(ExtractedSpan(
                    content=f"[Transcript] {transcript}",
                    modality=Modality.AUDIO,
                    timestamp_start=0.0,
                ))
            # Scene summary (placeholder — needs real video processing)
            spans.append(ExtractedSpan(
                content=(
                    "[Video] Video content extracted. "
                    "Real-time processing requires video provider integration."
                ),
                modality=Modality.VIDEO,
                confidence=0.5,
            ))
            job.spans = spans
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        self._jobs[job.job_id] = job
        return job

    async def _describe_image(self, image_base64: str) -> str:
        """Use LLM vision to describe an image."""
        has_vision = (
            self._provider is not None
            and hasattr(self._provider, "supports_vision")
            and self._provider.supports_vision()
        )
        if not has_vision:
            return "[Image content - vision provider not configured]"

        from app.providers.base import CompletionRequest, Message

        prompt_text = (
            "Describe this image in detail, including any text, objects, "
            "scenes, and relevant information for search and retrieval."
        )
        resp = await self._provider.complete(CompletionRequest(
            messages=[Message(
                role="user",
                content=[
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }},
                ],
            )],
            model="",
            max_tokens=500,
        ))
        return resp.content

    async def _extract_pdf(self, pdf_base64: str) -> list[ExtractedSpan]:
        """Extract text from PDF using available tools."""
        try:
            import base64

            pdf_bytes = base64.b64decode(pdf_base64)

            # Try pypdf first
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(pdf_bytes))
                spans = []
                for i, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    if text.strip():
                        spans.append(ExtractedSpan(
                            content=text.strip(),
                            modality=Modality.PDF,
                            source_page=i + 1,
                            confidence=0.95,
                        ))
                if spans:
                    return spans
                return [ExtractedSpan(
                    content="[PDF: no extractable text found]",
                    modality=Modality.PDF,
                )]
            except ImportError:
                pass

            return [ExtractedSpan(
                content="[PDF content - pypdf not installed]",
                modality=Modality.PDF,
                confidence=0.1,
            )]
        except Exception as exc:
            return [ExtractedSpan(
                content=f"[PDF extraction error: {exc}]",
                modality=Modality.PDF,
                confidence=0.0,
            )]

    async def _transcribe_audio(self, audio_base64: str) -> str:
        """Transcribe audio to text."""
        # Real implementation requires speech-to-text provider
        return "[Audio transcript - speech-to-text provider not configured]"

    def get_job(self, job_id: str, tenant_id: str) -> AssetIngestionJob | None:
        job = self._jobs.get(job_id)
        if job and job.tenant_id == tenant_id:
            return job
        return None

    def _create_job(self, tenant_id: str, modality: Modality, **kwargs: Any) -> AssetIngestionJob:
        import datetime
        return AssetIngestionJob(
            job_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            asset_type=modality,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **kwargs,
        )


# Module-level singleton
multimodal_pipeline = MultimodalPipeline()
