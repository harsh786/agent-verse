"""KB-17: PII redaction is linear-ish and leaves order / part numbers alone.

``RegexPIIAnalyzer.redact`` rebuilt the whole string once per finding
(O(N*F)) and overlap-checked every earlier finding (O(F^2)); the PHONE rule
redacted any bare 10-15 digit run starting 6-9, corrupting order and part
numbers.
"""

from __future__ import annotations

import time
from collections.abc import Callable

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


_UNIT = "Contact jane.doe{i}@example.com about card 4111 1111 1111 1111. " + "filler " * 60

# Work grows 4x between the two sizes: a linear redactor takes ~4x as long, the old
# quadratic one 11-16x (measured: 4.1x vs >= 10.8x at these sizes). The bound sits
# between them.
_GROWTH = 4
_MAX_LINEAR_RATIO = 7.0


def _document(units: int) -> str:
    return "".join(_UNIT.format(i=i) for i in range(units))


def _growth_ratio(work: Callable[[int], object], n: int, *, repeats: int = 3) -> float:
    """time(work(GROWTH*n)) / time(work(n)), each the best of ``repeats`` runs.

    The two sizes are measured interleaved and the minimum kept, so machine load
    (other processes, a busy CI box) slows both sides alike and mostly cancels
    out of the ratio — unlike a fixed wall-clock bound.
    """
    small: list[float] = []
    large: list[float] = []
    for _ in range(repeats):
        for size, samples in ((n, small), (_GROWTH * n, large)):
            started = time.perf_counter()
            work(size)
            samples.append(time.perf_counter() - started)
    return min(large) / min(small)


def test_growth_ratio_tells_linear_from_quadratic() -> None:
    """The harness itself: sleeps scale exactly, independent of CPU load."""
    assert _growth_ratio(lambda n: time.sleep(n * 0.002), 10) < _MAX_LINEAR_RATIO
    assert _growth_ratio(lambda n: time.sleep(n * n * 0.0002), 10) > _MAX_LINEAR_RATIO


def test_large_text_with_many_findings_redacts_in_linear_time_and_exactly() -> None:
    analyzer = RegexPIIAnalyzer()
    documents = {units: _document(units) for units in (2_500, _GROWTH * 2_500)}

    ratio = _growth_ratio(lambda units: analyzer.redact(documents[units]), 2_500)
    # The old implementation rebuilt the string per finding and overlap-checked
    # every earlier finding (O(N*F) + O(F^2)): >= 10.8x here.
    assert ratio < _MAX_LINEAR_RATIO, f"4x the text took {ratio:.1f}x as long (not linear)"

    redacted = analyzer.redact(documents[_GROWTH * 2_500])  # ~5 MB, 20k findings
    assert redacted.count("[REDACTED:EMAIL]") == _GROWTH * 2_500
    assert redacted.count("[REDACTED:CREDIT_CARD]") == _GROWTH * 2_500
    assert "@example.com" not in redacted


def test_overlapping_rules_still_prefer_the_more_specific_identifier() -> None:
    redacted = RegexPIIAnalyzer().redact("card 4111-1111-1111-1111 on file")
    assert redacted == "card [REDACTED:CREDIT_CARD] on file"
