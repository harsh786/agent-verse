"""Financial document field extractor (Invoice, Bank Statement, Receipt)."""

from __future__ import annotations

import re

from app.ocr.models import DocumentType, ExtractedField
from app.ocr.validators import normalize_date, validate_gstin

_HIGH_CONF = 0.9
_MED_CONF = 0.6


def _first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> tuple[str, float]:
    m = re.search(pattern, text, flags)
    if m:
        return m.group(1) if m.lastindex else m.group(0), _HIGH_CONF
    return "", 0.0


def _label_value(label: str, text: str) -> tuple[str, float]:
    pattern = rf"{re.escape(label)}[:\s]+([^\n]+)"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), _MED_CONF
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
        if self._doc_type == DocumentType.GSTIN_CERTIFICATE:
            return self._extract_gstin_certificate(raw_text)
        if self._doc_type == DocumentType.SALARY_SLIP:
            return self._extract_salary_slip(raw_text)
        if self._doc_type == DocumentType.ADDRESS_PROOF:
            return self._extract_address_proof(raw_text)
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
            normalized = normalize_date(val)
            if normalized:
                val = normalized
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

    def _extract_gstin_certificate(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        # GSTIN: 15-char alphanumeric
        val, conf = _first_match(r"(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d])", text, flags=0)
        if val:
            is_valid = validate_gstin(val)
            fields["gstin"] = ExtractedField(
                name="gstin", value=val, confidence=conf, is_valid=is_valid
            )
        val, conf = _label_value("Legal Name", text)
        if not val:
            val, conf = _label_value("Trade Name", text)
        if val:
            fields["business_name"] = ExtractedField(
                name="business_name", value=val, confidence=conf
            )
        val, conf = _label_value("Registration Date", text)
        if not val:
            val, conf = _first_match(r"(\d{2}/\d{2}/\d{4})", text)
        if val:
            fields["registration_date"] = ExtractedField(
                name="registration_date", value=val, confidence=conf
            )
        return fields

    def _extract_salary_slip(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _label_value("Employee Name", text)
        if not val:
            val, conf = _label_value("Name", text)
        if val:
            fields["employee_name"] = ExtractedField(
                name="employee_name", value=val, confidence=conf
            )
        val, conf = _label_value("Employer", text)
        if not val:
            val, conf = _label_value("Company", text)
        if val:
            fields["employer_name"] = ExtractedField(
                name="employer_name", value=val, confidence=conf
            )
        val, conf = _first_match(r"Gross\s+(?:Salary|Pay)[:\s]+([\d,]+\.?\d*)", text)
        if val:
            fields["gross_salary"] = ExtractedField(name="gross_salary", value=val, confidence=conf)
        val, conf = _first_match(r"Net\s+(?:Salary|Pay)[:\s]+([\d,]+\.?\d*)", text)
        if val:
            fields["net_pay"] = ExtractedField(name="net_pay", value=val, confidence=conf)
        # Month/Year
        val, conf = _first_match(
            r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s-]+\d{4})", text
        )
        if val:
            normalized = normalize_date(val)
            if normalized:
                val = normalized
            fields["pay_period"] = ExtractedField(name="pay_period", value=val, confidence=conf)
        return fields

    def _extract_address_proof(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _label_value("Name", text)
        if val:
            fields["name"] = ExtractedField(name="name", value=val, confidence=conf)
        val, conf = _label_value("Address", text)
        if val:
            fields["address"] = ExtractedField(name="address", value=val, confidence=conf)
        val, conf = _first_match(r"Bill\s+(?:Date|No)[:\s]+(\S+)", text)
        if val:
            fields["document_reference"] = ExtractedField(
                name="document_reference", value=val, confidence=conf
            )
        return fields
