"""Ingestion PII redaction: real identifiers are removed, ordinary words are not.

Regression: ingestion ran the guardrails' whole catalogue, so every knowledge-
base document lost words like "treatment", "condition", "therapy", "DNA" and
"fingerprint" (GDPR Art. 9 keyword signals), while spaced/dashed card numbers,
Indian mobile numbers, PAN and Aadhaar numbers went into the vector store.
"""

from __future__ import annotations

import pytest

from app.ingestion.pii import RegexPIIAnalyzer

_A = RegexPIIAnalyzer()


@pytest.mark.parametrize(
    "text",
    [
        "The water treatment plant is in good condition; therapy dogs visit on Fridays.",
        "Our DNA is customer focus. Fingerprint readers guard the server room.",
        "Order no 20261114 shipped on 2026-11-14; qty 42 at INR 7,499 (SKU-99812-X, INC-2041).",
        "Build 1234567, version 1.2.3, not a card: 1234 5678 9012 3456.",
        "Not an Aadhaar (bad checksum): 2345 6789 0123.",
    ],
)
def test_ordinary_business_text_is_untouched(text: str) -> None:
    assert _A.redact(text) == text
    assert _A.analyze(text) == []


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Card 4111 1111 1111 1111 on file", "CREDIT_CARD"),
        ("Card 4111-1111-1111-1111 on file", "CREDIT_CARD"),
        ("Card 4111111111111111 on file", "CREDIT_CARD"),
        ("Call +91 98765 43210 today", "PHONE"),
        ("Call 9876543210 today", "PHONE"),
        ("Call (415) 555-2671 today", "PHONE"),
        ("PAN ABCPE1234F on record", "PAN"),
        ("Aadhaar 2345 6789 0124 on record", "AADHAAR"),
        ("GSTIN 27ABCPE1234F1Z5 on record", "GSTIN"),
        ("mail priya.raman@northwind.example now", "EMAIL"),
        ("SSN 123-45-6789 on record", "SSN"),
        ("IFSC: HDFC0001234 branch", "ROUTING_NUMBER"),
        ("account number 123456789012 closed", "BANK_ACCOUNT"),
        ("DOB: 12/04/1990 verified", "DATE_OF_BIRTH"),
        ("password: hunter2secret here", "PASSWORD_INLINE"),
    ],
)
def test_identifiers_are_redacted(text: str, category: str) -> None:
    redacted = _A.redact(text)
    assert f"[REDACTED:{category}]" in redacted, redacted
    assert [f.category for f in _A.analyze(text)] == [category]
