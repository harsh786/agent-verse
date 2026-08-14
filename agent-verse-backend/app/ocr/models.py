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


@dataclass
class OcrResult:
    raw_text: str
    document_type: DocumentType
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    engine_used: Literal["tesseract", "llm_vision"] = "tesseract"
    overall_confidence: float = 0.0
    page_count: int = 1
