"""Multimodal data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"
    AUDIO = "audio"
    VIDEO = "video"
    OCR = "ocr"
    TABLE = "table"
    CODE = "code"


@dataclass
class ExtractedSpan:
    """A span of extracted content with source provenance."""

    content: str
    modality: Modality
    source_page: int | None = None
    source_frame: int | None = None
    timestamp_start: float | None = None
    timestamp_end: float | None = None
    bounding_box: dict[str, float] | None = None  # {x, y, width, height} normalized 0-1
    confidence: float = 1.0
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetIngestionJob:
    """A multimodal asset ingestion job.

    TODO(row-16/D-23): asset-ingestion jobs are currently held only in the
    in-memory ``MultimodalPipeline._jobs`` dict — they do not survive a process
    restart and are not shared across replicas. Persisting them requires a new
    ``asset_ingestion_jobs`` DB model + Alembic migration (with tenant RLS), which
    is out of scope for this change. Follow-up.
    """

    job_id: str
    tenant_id: str
    asset_type: Modality
    source_uri: str | None = None
    source_base64: str | None = None
    filename: str | None = None
    collection_id: str | None = None
    status: str = "pending"  # pending | processing | completed | failed
    spans: list[ExtractedSpan] = field(default_factory=list)
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
