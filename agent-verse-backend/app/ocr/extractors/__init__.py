"""Extractor registry — get_extractor(doc_type) factory."""
from __future__ import annotations

from app.ocr.extractors.base import OcrExtractor
from app.ocr.extractors.financial import FinancialExtractor
from app.ocr.extractors.general import GeneralExtractor
from app.ocr.extractors.id_docs import IdDocExtractor
from app.ocr.models import DocumentType

_ID_TYPES = {
    DocumentType.PAN_CARD,
    DocumentType.AADHAAR,
    DocumentType.PASSPORT,
    DocumentType.DRIVING_LICENSE,
    DocumentType.VOTER_ID,
    DocumentType.BANK_CHEQUE,
}

_FINANCIAL_TYPES = {
    DocumentType.INVOICE,
    DocumentType.BANK_STATEMENT,
    DocumentType.RECEIPT,
    DocumentType.GSTIN_CERTIFICATE,
    DocumentType.SALARY_SLIP,
    DocumentType.ADDRESS_PROOF,
}


def get_extractor(document_type: DocumentType) -> OcrExtractor:
    if document_type in _ID_TYPES:
        return IdDocExtractor(document_type)
    if document_type in _FINANCIAL_TYPES:
        return FinancialExtractor(document_type)
    return GeneralExtractor()
