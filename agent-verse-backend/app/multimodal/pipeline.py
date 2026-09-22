"""Multimodal ingestion pipeline."""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from typing import Any

from app.ai_router.model_orchestrator import ModelOrchestrator
from app.ingestion.content_classifier import ContentType
from app.multimodal.job_store import AssetJobStore
from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality

_log = logging.getLogger(__name__)

# A markdown table separator cell, e.g. "---", ":--", "--:", ":-:".
_MD_SEPARATOR_CELL = re.compile(r"^:?-+:?$")

# Binary attachments (image/pdf/audio/video) had no size cap at all in this
# pipeline -- an arbitrarily large base64 payload would be handed straight to
# a vision LLM / Whisper / ffmpeg with no bound, unlike the equivalent
# mission-attachment upload path (app/org/router.py's `_ATTACHMENT_MAX_BYTES`),
# which already rejects with a clear 413 at 25 MB. Mirror that limit here so
# oversized multimodal attachments fail fast with a clear error instead of
# silently costing an expensive provider call (or worse).
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB


def _decoded_b64_size(data_b64: str) -> int:
    """Approximate decoded byte size of a base64 string from its length
    alone (accounting for `=` padding), without fully decoding it -- the
    whole point is to bound cost for huge payloads before doing real work."""
    if not data_b64:
        return 0
    padding = len(data_b64) - len(data_b64.rstrip("="))
    return max(0, (len(data_b64) * 3) // 4 - padding)


class MultimodalPipeline:
    """Universal multimodal ingestion and extraction pipeline."""

    def __init__(
        self,
        *,
        job_store: AssetJobStore | None = None,
        model_orchestrator: ModelOrchestrator | None = None,
    ) -> None:
        self._provider: Any = None
        self._job_store = job_store or AssetJobStore()
        # D-14: the extractor model for IMAGE/AUDIO/VIDEO is chosen by the
        # ModelOrchestrator (provider-health-aware failover), not hardcoded.
        self._model_orchestrator = model_orchestrator or ModelOrchestrator()

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def set_job_store(self, job_store: AssetJobStore) -> None:
        """Swap in a (typically Redis-backed) job store, e.g. from the FastAPI
        lifespan once a real connection is available (two-phase wiring)."""
        self._job_store = job_store

    async def ingest_text(
        self,
        content: str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        job = self._create_job(tenant_id, Modality.TEXT, collection_id=collection_id)
        job.spans = [ExtractedSpan(content=content, modality=Modality.TEXT, confidence=1.0)]
        job.metadata["embedding_strategy"] = "direct_text_embed"
        job.status = "completed"
        await self._job_store.save(job)
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
        if self._reject_if_oversized(job, image_base64):
            await self._job_store.save(job)
            return job

        assignment = self._model_orchestrator.select_for_content_type(ContentType.IMAGE)
        job.metadata["extractor_model"] = assignment.extractor_model
        job.metadata["extractor_modality"] = assignment.modality

        try:
            job.status = "processing"
            description = await self._describe_image(image_base64, model=assignment.extractor_model)
            job.spans = [
                ExtractedSpan(content=description, modality=Modality.IMAGE, confidence=0.9)
            ]
            # D-11: this is NOT a native image embedding. The image is
            # described by a vision-capable LLM and the *caption text* is
            # what actually gets embedded downstream — VoyageProvider.embed
            # (and every other configured embedder) is text-only. Label this
            # honestly rather than letting callers assume a real multimodal
            # (pixel-level) embedding was computed.
            job.metadata["embedding_strategy"] = "caption_then_text_embed"
            job.metadata["real_multimodal_embedding"] = False
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("Image ingestion failed: %s", exc)

        await self._job_store.save(job)
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
        if self._reject_if_oversized(job, pdf_base64):
            await self._job_store.save(job)
            return job

        try:
            job.status = "processing"
            spans = await self._extract_pdf(pdf_base64)
            job.spans = spans
            job.metadata["embedding_strategy"] = "direct_text_embed"
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("PDF ingestion failed: %s", exc)

        await self._job_store.save(job)
        return job

    async def ingest_audio(
        self,
        audio_base64: str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        job = self._create_job(tenant_id, Modality.AUDIO, collection_id=collection_id)
        if self._reject_if_oversized(job, audio_base64):
            await self._job_store.save(job)
            return job

        assignment = self._model_orchestrator.select_for_content_type(ContentType.AUDIO)
        job.metadata["extractor_model"] = assignment.extractor_model
        job.metadata["extractor_modality"] = assignment.modality

        try:
            job.status = "processing"
            transcript = await self._transcribe_audio(audio_base64)
            if transcript.strip():
                job.spans = [
                    ExtractedSpan(
                        content=transcript,
                        modality=Modality.AUDIO,
                        confidence=0.85,
                        metadata={"extractor": "whisper-1"},
                    )
                ]
                # D-11: same honesty requirement as images -- the *transcript*
                # text is what gets embedded, not a native audio embedding.
                job.metadata["embedding_strategy"] = "transcript_then_text_embed"
                job.metadata["real_multimodal_embedding"] = False
            else:
                # No fabricated span -- record the gap honestly.
                job.spans = []
                job.metadata["audio_transcription"] = "unavailable"
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        await self._job_store.save(job)
        return job

    async def ingest_video(
        self,
        video_base64: str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        """Extract an audio transcript from video. Visual/scene analysis is
        not implemented and is gated honestly rather than faked (see below)."""
        job = self._create_job(tenant_id, Modality.VIDEO, collection_id=collection_id)
        if self._reject_if_oversized(job, video_base64):
            await self._job_store.save(job)
            return job

        assignment = self._model_orchestrator.select_for_content_type(ContentType.VIDEO)
        job.metadata["extractor_model"] = assignment.extractor_model
        job.metadata["extractor_modality"] = assignment.modality

        try:
            job.status = "processing"
            spans = []
            # Transcript -- the audio track is transcribed for real (Whisper).
            transcript = await self._transcribe_audio(video_base64)
            if transcript.strip():
                spans.append(
                    ExtractedSpan(
                        content=f"[Transcript] {transcript}",
                        modality=Modality.AUDIO,
                        timestamp_start=0.0,
                        metadata={"extractor": "whisper-1"},
                    )
                )
            # Visual/scene analysis is NOT implemented -- gate it honestly
            # rather than emitting a placeholder string as if it were
            # extracted data. The router-selected extractor model is recorded
            # so it is ready to use once visual extraction is implemented.
            job.metadata["video_visual_processing"] = "unavailable"
            job.metadata["video_frames_extracted"] = 0
            spans.append(
                ExtractedSpan(
                    content=(
                        "[Video visual analysis unavailable -- keyframe/scene "
                        "extraction requires a video-capable provider integration]"
                    ),
                    modality=Modality.VIDEO,
                    confidence=0.0,
                )
            )
            job.spans = spans
            job.metadata["embedding_strategy"] = "transcript_then_text_embed"
            job.metadata["real_multimodal_embedding"] = False
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        await self._job_store.save(job)
        return job

    async def ingest_code(
        self,
        content: str,
        tenant_id: str,
        language: str | None = None,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        """Ingest source code, preserving structure (function/class boundaries).

        Code is chunked AST-aware (reusing the ingestion AST chunker) so
        symbol boundaries survive rather than being flattened into prose.
        Each resulting span is tagged ``modality=CODE`` with the symbol type,
        name, and language recorded in metadata.
        """
        job = self._create_job(tenant_id, Modality.CODE, collection_id=collection_id)
        try:
            job.status = "processing"
            job.spans = self._extract_code_spans(content, language)
            job.metadata["embedding_strategy"] = "direct_text_embed"
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("Code ingestion failed: %s", exc)
        await self._job_store.save(job)
        return job

    async def ingest_table(
        self,
        source: list[dict[str, Any]] | str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        """Ingest a table (records, CSV, or a markdown table) as one structured span.

        The table is normalized to a markdown rendering (for retrieval) while
        the columns and row count are preserved in metadata, so tabular data
        stays structured rather than collapsing into flat, order-losing prose.
        """
        job = self._create_job(tenant_id, Modality.TABLE, collection_id=collection_id)
        try:
            job.status = "processing"
            columns, rows = self._normalize_table(source)
            markdown = self._table_to_markdown(columns, rows)
            job.spans = [
                ExtractedSpan(
                    content=markdown,
                    modality=Modality.TABLE,
                    confidence=1.0,
                    metadata={
                        "columns": columns,
                        "row_count": len(rows),
                        "extractor": "structured",
                    },
                )
            ]
            job.metadata["embedding_strategy"] = "direct_text_embed"
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("Table ingestion failed: %s", exc)
        await self._job_store.save(job)
        return job

    def _extract_code_spans(self, content: str, language: str | None) -> list[ExtractedSpan]:
        from app.ingestion.chunkers.ast_chunker import ASTChunker

        chunks = ASTChunker().chunk(content)
        spans: list[ExtractedSpan] = []
        for chunk in chunks:
            metadata: dict[str, Any] = dict(chunk.metadata)
            if language is not None:
                metadata["language"] = language
            spans.append(
                ExtractedSpan(
                    content=chunk.content,
                    modality=Modality.CODE,
                    confidence=1.0,
                    language=language,
                    metadata=metadata,
                )
            )
        return spans

    def _normalize_table(
        self, source: list[dict[str, Any]] | str
    ) -> tuple[list[str], list[list[str]]]:
        """Normalize records / CSV / markdown-table input to (columns, rows)."""
        if isinstance(source, list):
            return self._records_to_table(source)
        stripped = source.lstrip()
        if stripped.startswith("|"):
            parsed = self._parse_markdown_table(source.splitlines())
            if parsed is not None:
                return parsed
        return self._csv_to_table(source)

    @staticmethod
    def _records_to_table(records: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
        columns: list[str] = []
        for record in records:
            for key in record:
                if key not in columns:
                    columns.append(key)
        rows = [[str(record.get(col, "")) for col in columns] for record in records]
        return columns, rows

    @staticmethod
    def _csv_to_table(text: str) -> tuple[list[str], list[list[str]]]:
        reader = csv.reader(io.StringIO(text))
        table = [row for row in reader if row]
        if not table:
            return [], []
        header = [cell.strip() for cell in table[0]]
        rows = [[cell.strip() for cell in row] for row in table[1:]]
        return header, rows

    @staticmethod
    def _parse_markdown_table(
        lines: list[str],
    ) -> tuple[list[str], list[list[str]]] | None:
        """Parse markdown table lines into (columns, data_rows), or None if invalid."""
        cell_rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in lines
            if line.strip().startswith("|")
        ]
        if len(cell_rows) < 2:
            return None
        header = cell_rows[0]
        separator = cell_rows[1]
        if not separator or not all(_MD_SEPARATOR_CELL.match(cell) for cell in separator):
            return None
        data_rows = cell_rows[2:]
        return header, data_rows

    @staticmethod
    def _table_to_markdown(columns: list[str], rows: list[list[str]]) -> str:
        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join(["---"] * len(columns)) + " |",
        ]
        for row in rows:
            cells = list(row) + [""] * (len(columns) - len(row))
            lines.append("| " + " | ".join(cells[: len(columns)]) + " |")
        return "\n".join(lines)

    def detect_and_extract_tables(self, text: str) -> list[ExtractedSpan]:
        """Heuristically detect markdown tables in *text* and return one span per table."""
        import re

        spans: list[ExtractedSpan] = []
        blocks = re.split(r"\n{2,}", text)
        for block in blocks:
            lines = [ln for ln in block.splitlines() if ln.strip().startswith("|")]
            if len(lines) < 3:
                continue
            parsed = self._parse_markdown_table(lines)
            if parsed is None:
                continue
            columns, rows = parsed
            markdown = self._table_to_markdown(columns, rows)
            spans.append(
                ExtractedSpan(
                    content=markdown,
                    modality=Modality.TABLE,
                    confidence=0.85,
                    metadata={
                        "columns": columns,
                        "row_count": len(rows),
                        "extractor": "heuristic-markdown",
                    },
                )
            )
        return spans

    async def _describe_image(self, image_base64: str, model: str = "") -> str:
        """Use LLM vision to describe an image.

        ``model`` is the extractor model chosen by
        ``ModelOrchestrator.select_for_content_type`` (D-14) -- callers no
        longer hardcode a vision model name here.
        """
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
        resp = await self._provider.complete(
            CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=[
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                            },
                        ],
                    )
                ],
                model=model,
                max_tokens=500,
            )
        )
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
                        spans.append(
                            ExtractedSpan(
                                content=text.strip(),
                                modality=Modality.PDF,
                                source_page=i + 1,
                                confidence=0.95,
                            )
                        )
                if spans:
                    return spans
                return [
                    ExtractedSpan(
                        content="[PDF: no extractable text found]",
                        modality=Modality.PDF,
                    )
                ]
            except ImportError:
                pass

            return [
                ExtractedSpan(
                    content="[PDF content - pypdf not installed]",
                    modality=Modality.PDF,
                    confidence=0.1,
                )
            ]
        except Exception as exc:
            return [
                ExtractedSpan(
                    content=f"[PDF extraction error: {exc}]",
                    modality=Modality.PDF,
                    confidence=0.0,
                )
            ]

    async def _transcribe_audio(self, audio_base64: str) -> str:
        """Transcribe audio to text via the real Whisper-backed AudioParser.

        Returns an empty string on failure rather than a fabricated stub, so
        callers never persist placeholder text as if it were a transcript.
        """
        import base64

        from app.ingestion.parsers.audio_parser import AudioParser

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as exc:
            _log.warning("audio_decode_failed: %s", exc)
            return ""
        result = await AudioParser().parse_bytes(audio_bytes, "audio", "audio/mpeg")
        if result.error:
            _log.warning("audio_transcription_failed: %s", result.error)
            return ""
        return result.transcript

    async def get_job(self, job_id: str, tenant_id: str) -> AssetIngestionJob | None:
        return await self._job_store.get(job_id, tenant_id)

    def _reject_if_oversized(self, job: AssetIngestionJob, data_b64: str) -> bool:
        """Fail *job* in place if ``data_b64`` exceeds ``_MAX_ATTACHMENT_BYTES``.

        Returns True (and marks the job failed with a clear error) when
        oversized, so callers can bail out before doing any expensive work
        (LLM vision call, Whisper transcription, ffmpeg). Returns False for
        an attachment within the limit, leaving the job untouched.
        """
        size_bytes = _decoded_b64_size(data_b64)
        if size_bytes <= _MAX_ATTACHMENT_BYTES:
            return False
        limit_mb = _MAX_ATTACHMENT_BYTES // (1024 * 1024)
        job.status = "failed"
        job.error = (
            f"Attachment exceeds the {limit_mb} MB limit "
            f"(~{size_bytes / (1024 * 1024):.1f} MB)."
        )
        _log.warning(
            "multimodal_attachment_oversized: modality=%s size_bytes=%d limit_bytes=%d",
            job.asset_type,
            size_bytes,
            _MAX_ATTACHMENT_BYTES,
        )
        return True

    def _create_job(self, tenant_id: str, modality: Modality, **kwargs: Any) -> AssetIngestionJob:
        import datetime

        return AssetIngestionJob(
            job_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            asset_type=modality,
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            **kwargs,
        )


# Module-level singleton (in-memory only -- app.state.multimodal_pipeline,
# wired in app.main.create_app, is the DI'd instance used by the API layer
# and upgraded to Redis-backed persistence in the lifespan).
multimodal_pipeline = MultimodalPipeline()
