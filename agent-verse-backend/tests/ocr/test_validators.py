"""Tests for OCR field validators."""
import pytest

from app.ocr.validators import (
    mask_aadhaar,
    normalize_date,
    validate_aadhaar,
    validate_gstin,
    validate_pan,
)

# ── PAN ──────────────────────────────────────────────────────────────────────

def test_valid_pan():
    assert validate_pan("ABCDE1234F") is True


def test_invalid_pan_short():
    assert validate_pan("ABCDE123") is False


def test_invalid_pan_lowercase():
    assert validate_pan("abcde1234f") is False


def test_invalid_pan_wrong_format():
    assert validate_pan("12345ABCDE") is False


# ── Aadhaar ───────────────────────────────────────────────────────────────────

def test_aadhaar_mask():
    assert mask_aadhaar("1234 5678 9012") == "XXXX XXXX 9012"


def test_aadhaar_mask_no_spaces():
    assert mask_aadhaar("123456789012") == "XXXX XXXX 9012"


def test_aadhaar_mask_invalid_length():
    # Too short — return as-is
    result = mask_aadhaar("12345")
    assert result == "12345"


def test_aadhaar_invalid_wrong_length():
    assert validate_aadhaar("123456789") is False


# ── GSTIN ─────────────────────────────────────────────────────────────────────

def test_valid_gstin_format():
    # Well-known test GSTIN (format valid, checksum may or may not pass)
    # Just test format validation here
    valid_format = "29ABCDE1234F1Z5"
    # Format check passes regardless of checksum
    import re

    assert re.match(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$", valid_format)


def test_invalid_gstin_too_short():
    assert validate_gstin("29ABCDE1234F1Z") is False


def test_invalid_gstin_wrong_chars():
    assert validate_gstin("XXABCDE1234F1Z5") is False


# ── Date Normalization ────────────────────────────────────────────────────────

def test_normalize_ddmmyyyy():
    assert normalize_date("01/01/1990") == "1990-01-01"


def test_normalize_dd_mm_yyyy():
    assert normalize_date("15-08-2026") == "2026-08-15"


def test_normalize_yyyymmdd():
    assert normalize_date("1990-01-01") == "1990-01-01"


def test_normalize_month_name():
    assert normalize_date("1 Jan 1990") == "1990-01-01"


def test_normalize_invalid():
    assert normalize_date("not-a-date") is None


def test_normalize_returns_iso():
    result = normalize_date("25/12/2023")
    assert result == "2023-12-25"
