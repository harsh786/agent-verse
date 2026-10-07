"""OCR result models and document type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class DocumentType(StrEnum):
    PAN_CARD = "pan_card"
    AADHAAR = "aadhaar"
    PASSPORT = "passport"
    DRIVING_LICENSE = "driving_license"
    VOTER_ID = "voter_id"
    GSTIN_CERTIFICATE = "gstin_certificate"
    BANK_CHEQUE = "bank_cheque"
    SALARY_SLIP = "salary_slip"
    ADDRESS_PROOF = "address_proof"
    INVOICE = "invoice"
    BANK_STATEMENT = "bank_statement"
    RECEIPT = "receipt"
    GENERAL = "general"


@dataclass
class ExtractedField:
    name: str
    value: str
    confidence: float  # 0.0-1.0
    is_valid: bool = True
    masked_value: str | None = None


@dataclass
class OcrResult:
    raw_text: str
    document_type: DocumentType
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    # "mixed" when some pages were read by Tesseract and others by LLM vision.
    engine_used: Literal["tesseract", "llm_vision", "mixed"] = "tesseract"
    overall_confidence: float = 0.0
    page_count: int = 1
    # Per-page provenance, in page order: which engine produced each page's text.
    page_engines: list[str] = field(default_factory=list)
    # Pages whose text came from LLM vision (its confidence is not measured).
    vision_pages: int = 0
    # False when no page's confidence was measured (every page came from LLM
    # vision): ``overall_confidence`` is then the configured assumption
    # (OCR_VISION_ASSUMED_CONFIDENCE), not a measurement.
    confidence_measured: bool = True
    # WS-6: universal-ingestion provenance. When an input format cannot be
    # rasterized to images for OCR, ``degraded`` is set and ``degradation_reason``
    # records why (honest metadata, never a silent drop). ``source_format`` names
    # the detected input class (image/pdf/office/…).
    degraded: bool = False
    degradation_reason: str | None = None
    source_format: str | None = None
    # a10-F243-03: 1-based page numbers that yielded no text, and the subset
    # that yielded none because the OCR engine failed on them (no LLM provider,
    # vision call failed, page could not be rendered) rather than being blank.
    # Any failed page also sets ``degraded`` with the reasons.
    empty_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
