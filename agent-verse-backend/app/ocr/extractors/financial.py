"""Financial document field extractor (Invoice, Bank Statement, Receipt)."""
from __future__ import annotations

import re

from app.ocr.models import DocumentType, ExtractedField

_HIGH_CONF = 0.9
_MED_CONF = 0.6


def _first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> tuple[str, float]:
    m = re.search(pattern, text, flags)
    if m:
        return m.group(1) if m.lastindex else m.group(0), _HIGH_CONF
    return "", 0.0


class FinancialExtractor:
    def __init__(self, document_type: DocumentType) -> None:
        self._doc_type = document_type

    def extract(self, raw_text: str) -> dict[str, ExtractedField]:
        if self._doc_type == DocumentType.INVOICE:
            return self._extract_invoice(raw_text)
        if self._doc_type == DocumentType.BANK_STATEMENT:
            return self._extract_bank_statement(raw_text)
        if self._doc_type == DocumentType.RECEIPT:
            return self._extract_receipt(raw_text)
        return {}

    def _extract_invoice(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"Invoice\s*[#No.:]*\s*(\S+)", text)
        if val:
            fields["invoice_number"] = ExtractedField(
                name="invoice_number", value=val, confidence=conf
            )
        val, conf = _first_match(r"(\d{2}[/-]\d{2}[/-]\d{4})", text)
        if val:
            fields["invoice_date"] = ExtractedField(name="invoice_date", value=val, confidence=conf)
        val, conf = _first_match(r"Total[:\s]+(?:Rs\.?|INR)?\s*([\d,]+\.?\d*)", text)
        if val:
            fields["total_amount"] = ExtractedField(name="total_amount", value=val, confidence=conf)
        val, conf = _first_match(r"(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d])", text, flags=0)
        if val:
            fields["gst_number"] = ExtractedField(name="gst_number", value=val, confidence=conf)
        return fields

    def _extract_bank_statement(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"A/?c\s*[#No.:]*\s*(\d+)", text)
        if val:
            fields["account_number"] = ExtractedField(
                name="account_number", value=val, confidence=conf
            )
        val, conf = _first_match(r"IFSC[:\s]+([A-Z]{4}0[A-Z0-9]{6})", text, flags=0)
        if val:
            fields["ifsc_code"] = ExtractedField(name="ifsc_code", value=val, confidence=conf)
        val, conf = _first_match(r"Opening\s+Balance[:\s]+([\d,]+\.?\d*)", text)
        if val:
            fields["opening_balance"] = ExtractedField(
                name="opening_balance", value=val, confidence=conf
            )
        val, conf = _first_match(r"Closing\s+Balance[:\s]+([\d,]+\.?\d*)", text)
        if val:
            fields["closing_balance"] = ExtractedField(
                name="closing_balance", value=val, confidence=conf
            )
        return fields

    def _extract_receipt(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"Receipt\s*[#No.:]*\s*(\S+)", text)
        if val:
            fields["receipt_number"] = ExtractedField(
                name="receipt_number", value=val, confidence=conf
            )
        val, conf = _first_match(r"Amount[:\s]+(?:Rs\.?|INR)?\s*([\d,]+\.?\d*)", text)
        if val:
            fields["amount"] = ExtractedField(name="amount", value=val, confidence=conf)
        return fields
