"""OCR field validators: format checking, checksum validation, date normalization."""

from __future__ import annotations

import re
from datetime import datetime

# ── PAN Validation ────────────────────────────────────────────────────────────

_PAN_PATTERN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


def validate_pan(value: str) -> bool:
    """Validate PAN card number format: 5 uppercase letters, 4 digits, 1 uppercase letter."""
    return bool(_PAN_PATTERN.match(value.strip()))


# ── Aadhaar Validation (Verhoeff checksum) ───────────────────────────────────

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def validate_aadhaar(value: str) -> bool:
    """Validate 12-digit Aadhaar number using Verhoeff checksum."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 12:
        return False
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(d)]]
    return c == 0


def mask_aadhaar(value: str) -> str:
    """Mask first 8 digits of Aadhaar: XXXX XXXX 9012."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 12:
        return value  # cannot mask, return as-is
    return f"XXXX XXXX {digits[8:]}"


# ── GSTIN Validation ──────────────────────────────────────────────────────────

_GSTIN_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$")

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def validate_gstin(value: str) -> bool:
    """Validate GSTIN format and checksum."""
    v = value.strip().upper()
    if not _GSTIN_PATTERN.match(v):
        return False
    # Luhn-style checksum on first 14 chars to verify 15th
    total = 0
    for i, ch in enumerate(v[:14]):
        idx = _GSTIN_CHARS.index(ch)
        if i % 2 != 0:
            idx *= 2
        total += idx // len(_GSTIN_CHARS) + idx % len(_GSTIN_CHARS)
    check = (len(_GSTIN_CHARS) - total % len(_GSTIN_CHARS)) % len(_GSTIN_CHARS)
    return _GSTIN_CHARS[check] == v[14]


# ── Date Normalization ────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d %b, %Y",
]


def normalize_date(value: str) -> str | None:
    """Normalize any date string to ISO 8601 (YYYY-MM-DD). Returns None if unparseable."""
    v = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
