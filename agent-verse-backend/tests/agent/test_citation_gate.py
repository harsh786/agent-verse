"""Hallucination T4 — citation gate on synthesized answers."""
from __future__ import annotations

from app.agent.citation_gate import enforce_citations


def test_valid_cited_answer_passes() -> None:
    steps = {1: "We closed JIRA-101 and 7 tickets today."}
    ans = "Closed JIRA-101 [Step 1]. Completed 7 tickets [Step 1]."
    res = enforce_citations(ans, steps)
    assert res.ok is True
    assert res.dropped_sentences == 0
    assert "JIRA-101" in res.gated_answer


def test_uncited_claim_is_a_violation_and_stripped() -> None:
    steps = {1: "JIRA-101 is open."}
    ans = "JIRA-101 is open [Step 1]. There are 42 open bugs."  # 2nd has claim, no cite
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert res.dropped_sentences == 1
    assert "42 open bugs" not in res.gated_answer
    assert "JIRA-101 is open" in res.gated_answer


def test_wrong_citation_is_rejected() -> None:
    steps = {1: "JIRA-101 is open.", 2: "The report is ready."}
    ans = "There are 99 incidents [Step 2]."  # cited step has no such number
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert res.gated_answer == ""  # only sentence dropped
    assert any("99" in v for v in res.violations)


def test_prose_without_claims_is_kept() -> None:
    res = enforce_citations("This looks complete and well organized.", {1: "x"})
    assert res.ok is True
    assert "well organized" in res.gated_answer


def test_number_format_drift_still_grounds_citation() -> None:
    steps = {1: "Revenue was 1,200,000 this quarter."}
    ans = "Revenue was 1200000 [Step 1]."
    res = enforce_citations(ans, steps)
    assert res.ok is True
