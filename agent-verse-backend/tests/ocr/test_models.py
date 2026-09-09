"""Tests for OCR data models."""
import pytest

from app.ocr.models import DocumentType, ExtractedField, OcrResult


def test_document_type_values():
    assert DocumentType.PAN_CARD == "pan_card"
    assert DocumentType.AADHAAR == "aadhaar"
    assert DocumentType.PASSPORT == "passport"
    assert DocumentType.DRIVING_LICENSE == "driving_license"
    assert DocumentType.INVOICE == "invoice"
    assert DocumentType.BANK_STATEMENT == "bank_statement"
    assert DocumentType.RECEIPT == "receipt"
    assert DocumentType.GENERAL == "general"


def test_document_type_is_str():
    assert isinstance(DocumentType.PAN_CARD, str)


def test_extracted_field_defaults():
    f = ExtractedField(name="pan_number", value="ABCDE1234F", confidence=0.97)
    assert f.name == "pan_number"
    assert f.value == "ABCDE1234F"
    assert f.confidence == 0.97


def test_ocr_result_defaults():
    result = OcrResult(
        raw_text="some text",
        document_type=DocumentType.GENERAL,
    )
    assert result.fields == {}
    assert result.engine_used == "tesseract"
    assert result.overall_confidence == 0.0
    assert result.page_count == 1


def test_ocr_result_with_fields():
    fields = {
        "pan_number": ExtractedField(name="pan_number", value="ABCDE1234F", confidence=0.9)
    }
    result = OcrResult(
        raw_text="PERMANENT ACCOUNT NUMBER ABCDE1234F",
        document_type=DocumentType.PAN_CARD,
        fields=fields,
        engine_used="tesseract",
        overall_confidence=0.92,
        page_count=1,
    )
    assert result.document_type == DocumentType.PAN_CARD
    assert "pan_number" in result.fields
    assert result.fields["pan_number"].value == "ABCDE1234F"


def test_ocr_result_fields_independent():
    """Each OcrResult gets its own fields dict."""
    r1 = OcrResult(raw_text="a", document_type=DocumentType.GENERAL)
    r2 = OcrResult(raw_text="b", document_type=DocumentType.GENERAL)
    r1.fields["test"] = ExtractedField(name="test", value="v", confidence=0.5)
    assert "test" not in r2.fields
