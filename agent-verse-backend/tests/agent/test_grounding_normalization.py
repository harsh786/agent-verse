"""Hallucination T1 — typed claim normalization in grounding."""
from __future__ import annotations

from app.agent.grounding import (
    GroundingChecker,
    check_grounding,
    claim_grounded_in,
)


def test_number_token_boundary_kills_false_positive() -> None:
    # '10' must NOT be considered grounded just because '2010' contains it.
    assert claim_grounded_in("10", "number", "the year was 2010") is False
    assert claim_grounded_in("10", "number", "there were 10 issues") is True


def test_number_thousands_separator_equivalence() -> None:
    assert claim_grounded_in("1024", "number", "we processed 1,024 rows") is True
    assert claim_grounded_in("1,024", "number", "we processed 1024 rows") is True


def test_date_format_drift_is_grounded() -> None:
    ev = "the release shipped on july 15, 2026 as planned"
    assert claim_grounded_in("2026-07-15", "date", ev) is True
    assert claim_grounded_in("2026-07-16", "date", ev) is False


def test_check_grounding_normalize_catches_wrong_number() -> None:
    # Output claims 50 issues; evidence says 5 → ungrounded under normalization,
    # whereas naive substring would (wrongly) ground '5' inside '50'.
    out = "Found 50 issues in the backlog."
    ev = ["The backlog contains 5 issues."]
    res = check_grounding(out, ev, normalize=True, strict=True)
    assert res.grounded is False
    assert "50" in res.ungrounded_claims


def test_check_grounding_normalize_grounds_formatted_number() -> None:
    out = "Total revenue was 1200000."
    ev = ["Total revenue was 1,200,000 for the quarter."]
    res = check_grounding(out, ev, normalize=True, strict=True)
    assert res.grounded is True


async def test_grounding_checker_uses_normalization() -> None:
    checker = GroundingChecker(strict=True)
    res = await checker.check("Closed 10 tickets", ["We closed 10 tickets today"])
    assert res.grounded is True
    res2 = await checker.check("Closed 10 tickets", ["Report from 2010: nothing closed"])
    assert res2.grounded is False
