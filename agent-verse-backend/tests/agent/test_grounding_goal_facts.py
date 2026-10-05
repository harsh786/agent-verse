"""P5-6: goal-provided facts are evidence; ungrounded markers never split tokens.

P0 baseline §4.3 (GOAL-HIGH-RISK-APPROVE): the facts the user put in the goal
("rec-101 … last_used=2025-01-03") were flagged ungrounded — grounding evidence was
only tool outputs + KB context — and CORE-03 forced zero tolerance on the
high-risk goal, so a correct answer was replanned until the goal failed. The
number regex also re-extracted pieces of a date ("2026", "01" of "2026-01-01")
and ``annotate_ungrounded`` spliced a marker after the first occurrence of each:
"before 2026 [UNGROUNDED CLAIM …]-01 [UNGROUNDED CLAIM …]-01".
"""

from __future__ import annotations

import re

import pytest

from app.agent.grounding import (
    GroundingResult,
    annotate_ungrounded,
    check_grounding,
    extract_claims,
)
from app.agent.graph import AgentGraph
from app.agent.nodes._helpers import collect_grounding_sources
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="p5-6-tenant", plan=PlanTier.ENTERPRISE, api_key_id="k1")

GOAL = (
    "Demo list (in-memory sample data, no external systems or tools needed): "
    "rec-101 env=staging last_used=2025-01-03; rec-102 env=production last_used=2026-09-28; "
    "rec-103 env=staging last_used=2025-02-11; rec-104 env=staging last_used=2026-09-30. "
    "Delete the stale staging records (env=staging and last_used before 2026-01-01) from the "
    "demo list and report exactly which record IDs were removed and which remain."
)
ANSWER = (
    "Removed rec-101 (last used 2025-01-03) and rec-103 (last used 2025-02-11), both "
    "staging and unused since before 2026-01-01. Remaining: rec-102 and rec-104."
)


# ── claim extraction ────────────────────────────────────────────────────────


def test_number_claims_inside_a_date_are_not_extracted_twice() -> None:
    claims = extract_claims("Records used before 2026-01-01 were removed.")
    assert claims.get("date") == ["2026-01-01"]
    assert "number" not in claims


def test_number_claims_inside_urls_and_ids_are_not_extracted() -> None:
    claims = extract_claims("See https://tracker.test/items/48213 and JIRA-4471 for 3 more.")
    assert "48213" not in claims.get("number", [])
    assert "4471" not in claims.get("number", [])


def test_standalone_numbers_are_still_claims() -> None:
    assert extract_claims("We found 1290 rows and 42 errors.")["number"] == ["1290", "42"]


# ── goal-provided facts are evidence ────────────────────────────────────────


def test_goal_text_is_a_grounding_source() -> None:
    sources = collect_grounding_sources([], "", goal=GOAL)
    assert any("2025-01-03" in s for s in sources)
    result = check_grounding(ANSWER, sources, strict=True)
    assert result.grounded, result.ungrounded_claims


def test_invented_fact_is_still_ungrounded_with_the_goal_as_evidence() -> None:
    sources = collect_grounding_sources([], "", goal=GOAL)
    result = check_grounding("Also removed rec-977 last used 2024-12-31.", sources, strict=True)
    assert not result.grounded
    assert "2024-12-31" in result.ungrounded_claims


def _verify_graph() -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["out"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "done"}']),
    )


async def test_high_risk_answer_repeating_goal_facts_is_not_replanned() -> None:
    state = AgentState(goal=GOAL, tenant_ctx=T)
    state.cited_answer = ANSWER
    state.steps.append(StepResult(description="remove", status=StepStatus.COMPLETE, output=ANSWER))
    out = await _verify_graph()._node_verify({"agent_state": state, "tenant_ctx": T})
    s = out["agent_state"]
    assert s.verification_success is True, s.verification_feedback
    assert s.context.get("final_answer_grounded") is True
    assert "[UNGROUNDED" not in s.cited_answer


async def test_high_risk_answer_with_an_invented_fact_still_replans() -> None:
    state = AgentState(goal=GOAL, tenant_ctx=T)
    state.cited_answer = ANSWER + " Also removed rec-977 (last used 2024-12-31)."
    state.steps.append(StepResult(description="remove", status=StepStatus.COMPLETE, output="x"))
    out = await _verify_graph()._node_verify({"agent_state": state, "tenant_ctx": T})
    s = out["agent_state"]
    assert s.verification_success is False
    assert "2024-12-31" in s.ungrounded_claims


# ── markers at sentence / claim boundaries ──────────────────────────────────


def _result(*claims: str) -> GroundingResult:
    return GroundingResult(
        grounded=False, ungrounded_claims=list(claims), checked_claims=len(claims),
        evidence_length=0,
    )


def test_p0_marker_splice_is_gone() -> None:
    text = "The stale records are those last used before 2026-01-01."
    out = annotate_ungrounded(text, check_grounding(text, ["unrelated evidence"], strict=True))
    assert "2026-01-01" in out  # the token survives intact
    assert "]-01" not in out
    assert out.count("[UNGROUNDED CLAIM") == 1
    assert out.endswith("2026-01-01 [UNGROUNDED CLAIM — not found in the evidence: 2026-01-01].")


def test_markers_land_at_the_end_of_each_claims_sentence() -> None:
    text = "Ticket JIRA-12 is open. The fix shipped in PR 77! Nothing else changed"
    out = annotate_ungrounded(text, _result("JIRA-12", "77"))
    assert out == (
        "Ticket JIRA-12 is open [UNGROUNDED CLAIM — not found in the evidence: JIRA-12]. "
        "The fix shipped in PR 77 [UNGROUNDED CLAIM — not found in the evidence: 77]! "
        "Nothing else changed"
    )


def test_claims_in_one_sentence_share_one_marker_and_tokens_stay_whole() -> None:
    text = "In 2010 we had 10 servers and 2010 racks\nDone."
    out = annotate_ungrounded(text, _result("10", "2010"))
    # no marker inside a token: every marker is preceded by a space + whole word
    for m in re.finditer(r"\[UNGROUNDED CLAIM", out):
        assert out[m.start() - 1] == " "
    assert out.count("[UNGROUNDED CLAIM") == 1
    assert out.startswith("In 2010 we had 10 servers and 2010 racks [UNGROUNDED CLAIM")
    assert out.endswith("]\nDone.")


@pytest.mark.parametrize(
    "text",
    [
        "Version 3.5 of https://example.test/a.b ships on 2026-03-01 with 1290 fixes.",
        "Total: $1,024.50 across 12 invoices.",
    ],
)
def test_decimal_points_and_urls_are_not_sentence_ends(text: str) -> None:
    claims = [c for vals in extract_claims(text).values() for c in vals]
    out = annotate_ungrounded(text, _result(*claims))
    assert out.count("[UNGROUNDED CLAIM") == 1
    assert out.rstrip(".").endswith("]")
