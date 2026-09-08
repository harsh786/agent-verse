"""Multimodal ingestion pipeline."""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from typing import Any

from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality

# A markdown separator cell, e.g. "---", ":--", "--:", ":-:".
_MD_SEPARATOR_CELL = re.compile(r"^:?-+:?$")

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
            if transcript.strip():
                job.spans = [
                    ExtractedSpan(content=transcript, modality=Modality.AUDIO, confidence=0.85)
                ]
            else:
                # No fabricated span — record the gap honestly.
                job.spans = []
                job.metadata["audio_transcription"] = "unavailable"
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
            # Transcript — the audio track is transcribed for real.
            transcript = await self._transcribe_audio(video_base64)
            if transcript.strip():
                spans.append(
                    ExtractedSpan(
                        content=f"[Transcript] {transcript}",
                        modality=Modality.AUDIO,
                        timestamp_start=0.0,
                    )
                )
            # Visual/scene analysis is NOT implemented — gate it honestly rather
            # than emitting a placeholder string as if it were extracted data.
            job.metadata["video_visual_processing"] = "unavailable"
            job.metadata["video_frames_extracted"] = 0
            spans.append(
                ExtractedSpan(
                    content=(
                        "[Video visual analysis unavailable — keyframe/scene "
                        "extraction requires a video provider integration]"
                    ),
                    modality=Modality.VIDEO,
                    confidence=0.0,
                )
            )
            job.spans = spans
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        self._jobs[job.job_id] = job
        return job

    async def ingest_code(
        self,
        content: str,
        tenant_id: str,
        language: str | None = None,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        """Ingest source code, preserving structure (function/class boundaries).

        Code is chunked AST-aware (reusing the ingestion AST chunker) so that
        symbol boundaries survive, rather than being flattened / reflowed as
        prose. Each resulting span is tagged ``modality=CODE`` with the symbol
        type, name and language recorded in metadata.
        """
        job = self._create_job(tenant_id, Modality.CODE, collection_id=collection_id)
        try:
            job.status = "processing"
            job.spans = self._extract_code_spans(content, language)
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("Code ingestion failed: %s", exc)
        self._jobs[job.job_id] = job
        return job

    async def ingest_table(
        self,
        source: list[dict[str, Any]] | str,
        tenant_id: str,
        collection_id: str | None = None,
    ) -> AssetIngestionJob:
        """Ingest a table (records, CSV, or a markdown table) as a structured span.

        The table is normalized to a markdown rendering (for retrieval) while the
        columns and row count are preserved in metadata, so tabular data is kept
        structured rather than collapsed to flat prose text.
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
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            _log.warning("Table ingestion failed: %s", exc)
        self._jobs[job.job_id] = job
        return job

    def detect_and_extract_tables(self, text: str) -> list[ExtractedSpan]:
        """Best-effort extraction of markdown-style table regions from free text.

        Contiguous blocks of ``| ... |`` lines that carry a separator row are
        pulled out as ``modality=TABLE`` spans. This is a heuristic (not a full
        table detector), so confidence is < 1.0 and the extractor used is
        recorded in metadata for honesty.
        """
        spans: list[ExtractedSpan] = []
        lines = text.splitlines()
        block: list[str] = []

        def _flush(candidate: list[str]) -> None:
            if len(candidate) < 2:
                return
            parsed = self._parse_markdown_table(candidate)
            if parsed is None:
                return
            columns, rows = parsed
            if not rows:
                return
            spans.append(
                ExtractedSpan(
                    content=self._table_to_markdown(columns, rows),
                    modality=Modality.TABLE,
                    confidence=0.7,
                    metadata={
                        "columns": columns,
                        "row_count": len(rows),
                        "extractor": "heuristic-markdown",
                    },
                )
            )

        for line in lines:
            if line.strip().startswith("|"):
                block.append(line)
            else:
                _flush(block)
                block = []
        _flush(block)
        return spans

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
                model="",
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
            created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            **kwargs,
        )


# Module-level singleton
multimodal_pipeline = MultimodalPipeline()
