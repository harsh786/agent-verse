"""ID document field extractor (PAN, Aadhaar, Passport, Driving License)."""
from __future__ import annotations

import re

from app.ocr.models import DocumentType, ExtractedField

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
        return {}

    def _extract_pan(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"([A-Z]{5}\d{4}[A-Z])", text)
        if val:
            fields["pan_number"] = ExtractedField(name="pan_number", value=val, confidence=conf)
        val, conf = _label_value("Name", text)
        if val:
            fields["name"] = ExtractedField(name="name", value=val, confidence=conf)
        val, conf = _first_match(r"(\d{2}/\d{2}/\d{4})", text)
        if val:
            fields["date_of_birth"] = ExtractedField(
                name="date_of_birth", value=val, confidence=conf
            )
        return fields

    def _extract_aadhaar(self, text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}
        val, conf = _first_match(r"(\d{4}\s\d{4}\s\d{4})", text)
        if val:
            fields["aadhaar_number"] = ExtractedField(
                name="aadhaar_number", value=val, confidence=conf
            )
        val, conf = _label_value("DOB", text)
        if not val:
            val, conf = _first_match(r"(\d{2}/\d{2}/\d{4})", text)
        if val:
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
            fields["date_of_birth"] = ExtractedField(
                name="date_of_birth", value=dates[0], confidence=_HIGH_CONF
            )
        if len(dates) > 1:
            fields["expiry_date"] = ExtractedField(
                name="expiry_date", value=dates[1], confidence=_HIGH_CONF
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
            fields["valid_to"] = ExtractedField(name="valid_to", value=val, confidence=conf)
        return fields
