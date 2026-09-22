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


# ===========================================================================
# Edge cases: invalidated sources, malformed citations, out-of-bounds refs,
# duplicate citations, and interaction with the grounding gate.
# ===========================================================================


def test_citation_to_invalidated_or_expired_source_is_a_violation() -> None:
    """A step that was retrieved earlier but whose output was later cleared/
    invalidated (e.g. redacted, expired cache entry) must not ground a claim —
    an empty step output behaves the same as no supporting evidence."""
    steps = {1: ""}  # step existed, but its content was invalidated/expired
    ans = "The ticket count was 42 [Step 1]."
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert res.gated_answer == ""
    assert any("42" in v for v in res.violations)


def test_malformed_citation_no_brackets_is_treated_as_uncited() -> None:
    """'Step 1' without brackets is not a recognized citation — the claim must
    be treated as uncited, not silently accepted."""
    steps = {1: "42 issues were closed."}
    ans = "Closed 42 issues Step 1."
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert res.gated_answer == ""


def test_malformed_citation_missing_number_is_treated_as_uncited() -> None:
    """A citation-shaped fragment with no digits ('[Step]') must not match the
    step-ref regex and so counts as an uncited claim."""
    steps = {1: "42 issues were closed."}
    ans = "Closed 42 issues [Step]."
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert any("42" in v for v in res.violations)


def test_citation_tight_no_space_before_number_is_still_recognized() -> None:
    """'[Step1]' (no space between the word and the number) must still parse —
    the LLM frequently omits the space and the gate should not punish that
    formatting quirk as if the claim were uncited."""
    steps = {1: "42 issues were closed."}
    ans = "Closed 42 issues [Step1]."
    res = enforce_citations(ans, steps)
    assert res.ok is True
    assert "42 issues" in res.gated_answer


def test_citation_index_out_of_bounds_is_a_violation() -> None:
    """Citing a step number that was never produced ([Step 99] when only step 1
    exists) must not ground the claim — lookup falls back to empty evidence."""
    steps = {1: "42 issues were closed."}
    ans = "There were 999 regressions [Step 99]."
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert res.gated_answer == ""
    assert any("999" in v for v in res.violations)


def test_citation_step_zero_out_of_bounds_is_a_violation() -> None:
    """Step numbering is 1-based; a claim citing [Step 0] must not accidentally
    match anything (no off-by-one fallback to the first real step)."""
    steps = {1: "42 issues were closed."}
    ans = "There were 42 issues [Step 0]."
    res = enforce_citations(ans, steps)
    assert res.ok is False


def test_multiple_citations_to_the_same_source_are_deduplicated_in_evidence() -> None:
    """Citing the same step twice ('[Step 1] [Step 1]') must not error and must
    ground correctly — repeated citations to one source are legitimate, not a
    format error."""
    steps = {1: "Revenue was 42 dollars and 7 units sold."}
    ans = "Revenue was 42 dollars [Step 1] [Step 1] and 7 units sold [Step 1]."
    res = enforce_citations(ans, steps)
    assert res.ok is True
    assert res.dropped_sentences == 0


def test_multiple_citations_combine_evidence_across_cited_steps() -> None:
    """A sentence citing two different steps must be checked against the
    UNION of both steps' evidence, not just one of them."""
    steps = {1: "The ticket JIRA-101 was opened.", 2: "It was closed after 7 days."}
    ans = "JIRA-101 was closed after 7 days [Step 1] [Step 2]."
    res = enforce_citations(ans, steps)
    assert res.ok is True


def test_grounding_gate_catches_claim_with_fabricated_citation_number() -> None:
    """A claim that cites a real step, but the cited step's content is about a
    different fact entirely (i.e. it is ungrounded despite the citation
    'looking' well-formed), must still be flagged — the citation gate checks
    substance, not merely presence of a [Step N] tag."""
    steps = {1: "The deployment to staging succeeded."}
    ans = "Production revenue grew by 87 percent [Step 1]."
    res = enforce_citations(ans, steps)
    assert res.ok is False
    assert any("87" in v for v in res.violations)


def test_empty_answer_is_trivially_ok() -> None:
    """An empty/whitespace-only answer has nothing to cite or violate — it
    must short-circuit to ok=True with an empty gated answer rather than
    being treated as an uncited claim."""
    res = enforce_citations("   ", {1: "some evidence"})
    assert res.ok is True
    assert res.gated_answer == ""
    assert res.violations == []


def test_strip_false_keeps_original_answer_even_with_violations() -> None:
    """When strip=False, the gate still reports violations but returns the
    original (unmodified) answer — callers that only want a verdict (not a
    rewritten answer) must get the full text back."""
    steps = {1: "JIRA-101 is open."}
    ans = "JIRA-101 is open [Step 1]. There are 42 open bugs."
    res = enforce_citations(ans, steps, strip=False)
    assert res.ok is False
    assert res.gated_answer == ans
    assert "42 open bugs" in res.gated_answer
