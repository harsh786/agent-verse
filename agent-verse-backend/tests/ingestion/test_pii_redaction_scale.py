"""KB-17: PII redaction is linear-ish and leaves order / part numbers alone.

``RegexPIIAnalyzer.redact`` rebuilt the whole string once per finding
(O(N*F)) and overlap-checked every earlier finding (O(F^2)); the PHONE rule
redacted any bare 10-15 digit run starting 6-9, corrupting order and part
numbers.
"""

from __future__ import annotations

import time

import pytest

from app.ingestion.pii import RegexPIIAnalyzer


@pytest.mark.parametrize(
    "text",
    [
        "Order 9876543210 shipped on Monday.",
        "Part number 7012345678901 is back in stock.",
        "Invoice ref 8123456789 was paid.",
    ],
)
def test_bare_order_and_part_numbers_are_not_redacted(text: str) -> None:
    assert RegexPIIAnalyzer().redact(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Call 9876543210 today",
        "Mobile: 9876543210",
        "reach me on +91 98765 43210",
        "office (415) 555-2671",
        "tel 415-555-2671",
    ],
)
def test_real_phone_numbers_are_still_redacted(text: str) -> None:
    assert "[REDACTED:PHONE]" in RegexPIIAnalyzer().redact(text)


def test_large_text_with_many_findings_redacts_quickly_and_exactly() -> None:
    unit = "Contact jane.doe{i}@example.com about card 4111 1111 1111 1111. " + "filler " * 60
    text = "".join(unit.format(i=i) for i in range(10_000))  # ~5 MB, 20k findings
    analyzer = RegexPIIAnalyzer()

    started = time.perf_counter()
    redacted = analyzer.redact(text)
    elapsed = time.perf_counter() - started

    assert redacted.count("[REDACTED:EMAIL]") == 10_000
    assert redacted.count("[REDACTED:CREDIT_CARD]") == 10_000
    assert "@example.com" not in redacted
    assert elapsed < 3, f"redaction took {elapsed:.1f}s"  # was ~13 s (quadratic)


def test_overlapping_rules_still_prefer_the_more_specific_identifier() -> None:
    redacted = RegexPIIAnalyzer().redact("card 4111-1111-1111-1111 on file")
    assert redacted == "card [REDACTED:CREDIT_CARD] on file"
