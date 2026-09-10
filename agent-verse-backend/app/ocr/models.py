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
    engine_used: Literal["tesseract", "llm_vision"] = "tesseract"
    overall_confidence: float = 0.0
    page_count: int = 1
    # WS-6: universal-ingestion provenance. When an input format cannot be
    # rasterized to images for OCR, ``degraded`` is set and ``degradation_reason``
    # records why (honest metadata, never a silent drop). ``source_format`` names
    # the detected input class (image/pdf/office/…).
    degraded: bool = False
    degradation_reason: str | None = None
    source_format: str | None = None
