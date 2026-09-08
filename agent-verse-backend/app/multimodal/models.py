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

    Persisted via ``app.multimodal.job_store.AssetJobStore`` (D-23): the store
    is in-memory-only by default (matches this dataclass being lightweight and
    test-friendly) but is upgraded to a Redis-backed instance in the FastAPI
    lifespan when a Redis connection is available, so jobs survive a process
    restart and are visible across replicas.
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
