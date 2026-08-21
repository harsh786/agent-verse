"""ModalityPipeline — maps content type to full processing pipeline config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ingestion.content_classifier import ContentType


@dataclass
class ModalityPipelineResult:
    content_type: ContentType
    parser_class: Any
    chunker_strategy: str
    embedding_modality: str
    requires_transcription: bool = False
    requires_vision: bool = False
    metadata: dict = field(default_factory=dict)


class ModalityPipeline:
    def select_pipeline(self, ct: ContentType) -> ModalityPipelineResult:
        if ct == ContentType.PDF:
            return ModalityPipelineResult(ct, None, "layout", "text")
        if ct == ContentType.AUDIO:
            return ModalityPipelineResult(
                ct, None, "timestamp", "text", requires_transcription=True
            )
        if ct == ContentType.VIDEO:
            return ModalityPipelineResult(
                ct, None, "scene", "multimodal", requires_transcription=True
            )
        if ct == ContentType.IMAGE:
            return ModalityPipelineResult(ct, None, "region", "multimodal", requires_vision=True)
        if ct == ContentType.CODE:
            return ModalityPipelineResult(ct, None, "ast", "code")
        if ct == ContentType.DOCX:
            return ModalityPipelineResult(ct, None, "heading", "text")
        if ct == ContentType.CSV:
            return ModalityPipelineResult(ct, None, "row_group", "text")
        return ModalityPipelineResult(ct, None, "semantic", "text")
