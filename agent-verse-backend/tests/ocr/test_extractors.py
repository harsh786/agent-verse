"""Tests for OCR field extractors."""

from app.ocr.extractors import get_extractor
from app.ocr.extractors.general import GeneralExtractor
from app.ocr.models import DocumentType


def test_general_extractor_returns_empty():
    ext = GeneralExtractor()
    assert ext.extract("anything") == {}


def test_get_extractor_general():
    ext = get_extractor(DocumentType.GENERAL)
    assert isinstance(ext, GeneralExtractor)


# --- PAN Card ---

PAN_TEXT = "PERMANENT ACCOUNT NUMBER ABCDE1234F\nName: TEST USER\nDOB: 01/01/1990"


def test_pan_extracts_pan_number():
    ext = get_extractor(DocumentType.PAN_CARD)
    fields = ext.extract(PAN_TEXT)
    assert "pan_number" in fields
    assert fields["pan_number"].value == "ABCDE1234F"
    assert fields["pan_number"].confidence > 0


def test_pan_extracts_name():
    ext = get_extractor(DocumentType.PAN_CARD)
    fields = ext.extract(PAN_TEXT)
    assert "name" in fields
    assert fields["name"].value == "TEST USER"


def test_pan_extracts_dob():
    ext = get_extractor(DocumentType.PAN_CARD)
    fields = ext.extract(PAN_TEXT)
    assert "date_of_birth" in fields
    assert fields["date_of_birth"].value == "1990-01-01"


# --- Aadhaar ---

AADHAAR_TEXT = "UIDAI\n1234 5678 9012\nTest Name\nDOB: 01/01/1990"


def test_aadhaar_extracts_number():
    ext = get_extractor(DocumentType.AADHAAR)
    fields = ext.extract(AADHAAR_TEXT)
    assert "aadhaar_number" in fields
    assert fields["aadhaar_number"].value == "1234 5678 9012"


def test_aadhaar_extracts_dob():
    ext = get_extractor(DocumentType.AADHAAR)
    fields = ext.extract(AADHAAR_TEXT)
    assert "date_of_birth" in fields


def test_aadhaar_extracts_masked_value():
    ext = get_extractor(DocumentType.AADHAAR)
    fields = ext.extract(AADHAAR_TEXT)
    assert "aadhaar_number" in fields
    assert fields["aadhaar_number"].masked_value is not None
    assert "XXXX" in fields["aadhaar_number"].masked_value


# --- Invoice ---

INVOICE_TEXT = "Invoice No: INV-001\nTotal: Rs. 1,500.00\nGST: 29ABCDE1234F1Z5"


def test_invoice_extracts_invoice_number():
    ext = get_extractor(DocumentType.INVOICE)
    fields = ext.extract(INVOICE_TEXT)
    assert "invoice_number" in fields
    assert "INV" in fields["invoice_number"].value


def test_invoice_extracts_total_amount():
    ext = get_extractor(DocumentType.INVOICE)
    fields = ext.extract(INVOICE_TEXT)
    assert "total_amount" in fields
    assert "1,500" in fields["total_amount"].value


# --- Bank Statement ---

BANK_TEXT = "Account Statement\nA/c No: 1234567890\nOpening Balance: 10,000\nIFSC: SBIN0001234\nClosing Balance: 12,000"


def test_bank_extracts_account_number():
    ext = get_extractor(DocumentType.BANK_STATEMENT)
    fields = ext.extract(BANK_TEXT)
    assert "account_number" in fields
    assert fields["account_number"].value == "1234567890"


def test_bank_extracts_ifsc():
    ext = get_extractor(DocumentType.BANK_STATEMENT)
    fields = ext.extract(BANK_TEXT)
    assert "ifsc_code" in fields
    assert fields["ifsc_code"].value == "SBIN0001234"


def test_bank_extracts_balances():
    ext = get_extractor(DocumentType.BANK_STATEMENT)
    fields = ext.extract(BANK_TEXT)
    assert "opening_balance" in fields
    assert "closing_balance" in fields


# --- Receipt ---

RECEIPT_TEXT = "Receipt No: REC-001\nAmount: Rs. 500"


def test_receipt_extracts_number():
    ext = get_extractor(DocumentType.RECEIPT)
    fields = ext.extract(RECEIPT_TEXT)
    assert "receipt_number" in fields


def test_receipt_extracts_amount():
    ext = get_extractor(DocumentType.RECEIPT)
    fields = ext.extract(RECEIPT_TEXT)
    assert "amount" in fields
    assert "500" in fields["amount"].value
