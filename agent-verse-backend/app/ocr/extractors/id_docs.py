"""ID document field extractor (PAN, Aadhaar, Passport, Driving License)."""

from __future__ import annotations

import re

from app.ocr.models import DocumentType, ExtractedField
from app.ocr.validators import (
    mask_aadhaar,
    normalize_date,
    validate_aadhaar,
    validate_pan,
)

_HIGH_CONF = 0.9
_MED_CONF = 0.6


def _first_match(pattern: str, text: str, flags: int = 0) -> tuple[str, float]:
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


class IdDocExtractor:
    def __init__(self, document_type: DocumentType) -> None:
        self._doc_type = document_type

    def extract(self, raw_text: str) -> dict[str, ExtractedField]:
        if self._doc_type == DocumentType.PAN_CARD:
            return self._extract_pan(raw_text)
        if self._doc_type == DocumentType.AADHAAR:
            return self._extract_aadhaar(raw_text)
        if self._doc_type == DocumentType.PASSPORT:
            return self._extract_passport(raw_text)
        if self._doc_type == DocumentType.DRIVING_LICENSE:
            return self._extract_dl(raw_text)
        if self._doc_type == DocumentType.VOTER_ID:
            return self._extract_voter_id(raw_text)
        if self._doc_type == DocumentType.BANK_CHEQUE:
            return self._extract_cheque(raw_text)
        return {}

    def _extract_pan(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"([A-Z]{5}\d{4}[A-Z])", text)
        if val:
            is_valid = validate_pan(val)
            fields["pan_number"] = ExtractedField(
                name="pan_number", value=val, confidence=conf, is_valid=is_valid
            )
        val, conf = _label_value("Name", text)
        if val:
            fields["name"] = ExtractedField(name="name", value=val, confidence=conf)
        val, conf = _first_match(r"(\d{2}/\d{2}/\d{4})", text)
        if val:
            normalized = normalize_date(val)
            if normalized:
                val = normalized
            fields["date_of_birth"] = ExtractedField(
                name="date_of_birth", value=val, confidence=conf
            )
        return fields

    def _extract_aadhaar(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"(\d{4}\s\d{4}\s\d{4})", text)
        if val:
            is_valid = validate_aadhaar(val)
            masked = mask_aadhaar(val)
            fields["aadhaar_number"] = ExtractedField(
                name="aadhaar_number",
                value=val,
                confidence=conf,
                is_valid=is_valid,
                masked_value=masked,
            )
        val, conf = _label_value("DOB", text)
        if not val:
            val, conf = _first_match(r"(\d{2}/\d{2}/\d{4})", text)
        if val:
            normalized = normalize_date(val)
            if normalized:
                val = normalized
            fields["date_of_birth"] = ExtractedField(
                name="date_of_birth", value=val, confidence=conf
            )
        return fields

    def _extract_passport(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"([A-Z]\d{7})", text)
        if val:
            fields["passport_number"] = ExtractedField(
                name="passport_number", value=val, confidence=conf
            )
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", text)
        if dates:
            dob_val = normalize_date(dates[0]) or dates[0]
            fields["date_of_birth"] = ExtractedField(
                name="date_of_birth", value=dob_val, confidence=_HIGH_CONF
            )
        if len(dates) > 1:
            exp_val = normalize_date(dates[1]) or dates[1]
            fields["expiry_date"] = ExtractedField(
                name="expiry_date", value=exp_val, confidence=_HIGH_CONF
            )
        return fields

    def _extract_dl(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"(DL-\S+)", text)
        if not val:
            val, conf = _first_match(r"([A-Z]{2}\d{2}\s?\d{11})", text)
        if val:
            fields["dl_number"] = ExtractedField(name="dl_number", value=val, confidence=conf)
        val, conf = _label_value("Name", text)
        if val:
            fields["name"] = ExtractedField(name="name", value=val, confidence=conf)
        val, conf = _label_value("Valid Till", text)
        if val:
            normalized = normalize_date(val)
            if normalized:
                val = normalized
            fields["valid_to"] = ExtractedField(name="valid_to", value=val, confidence=conf)
        return fields

    def _extract_voter_id(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        # EPIC number format: letters + digits, often "ABC1234567"
        val, conf = _first_match(r"EPIC\s*(?:No\.?|:)?\s*([A-Z]{3}\d{7})", text)
        if not val:
            val, conf = _first_match(r"([A-Z]{3}\d{7})", text)
        if val:
            fields["epic_number"] = ExtractedField(name="epic_number", value=val, confidence=conf)
        val, conf = _label_value("Name", text)
        if val:
            fields["name"] = ExtractedField(name="name", value=val, confidence=conf)
        val, conf = _first_match(r"(\d{2}/\d{2}/\d{4})", text)
        if val:
            normalized = normalize_date(val)
            if normalized:
                val = normalized
            fields["date_of_birth"] = ExtractedField(
                name="date_of_birth", value=val, confidence=conf
            )
        return fields

    def _extract_cheque(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        # Cheque number: 6-digit
        val, conf = _first_match(r"Cheque\s*(?:No\.?|:)?\s*(\d{6,})", text)
        if not val:
            val, conf = _first_match(r"\b(\d{6})\b", text)
        if val:
            fields["cheque_number"] = ExtractedField(
                name="cheque_number", value=val, confidence=conf
            )
        # MICR code: 9-digit at bottom of cheque
        val, conf = _first_match(r"(\d{9})", text)
        if val:
            fields["micr_code"] = ExtractedField(name="micr_code", value=val, confidence=conf)
        # Amount
        val, conf = _first_match(r"(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)", text)
        if val:
            fields["amount"] = ExtractedField(name="amount", value=val, confidence=conf)
        val, conf = _label_value("Pay", text)
        if val:
            fields["payee"] = ExtractedField(name="payee", value=val, confidence=conf)
        return fields
