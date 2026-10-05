"""P5-5: risk-classifier false positives found by the P0 live baseline.

Read-only goals were gated (or denied in bounded-autonomous mode) because words in
free text were read as risky actions:

* "before **releasing** a cold-chain consignment" → RELEASE (MoA goal, §4.6)
* "database **migration** window" → migrate + DATA target (KB-REAL-DOCS, §4.14)
* "Quokka **Pay**" (a product name) → financial "pay" (§4.14)
* "Perform a web search for the **repo** rate **set by** the Reserve **Bank**" →
  mutating perform/set + infra/money targets (GOV-POLICY-APPROVAL, §4.10)

The fix reads action semantics — verb use vs. noun modifier / proper name /
reduced passive, the object of a release, and verb–target pairing within a clause —
while every true positive below stays gated.
"""

from __future__ import annotations

import pytest

from app.agent.risk_classifier import assess_step_risk, is_high_risk_text

# ── the P0 goal / step texts (verbatim) ─────────────────────────────────────

P0_MOA_GOAL = (
    "Draft a five-item checklist a dispatcher should follow before releasing a cold-chain "
    "pharmaceutical consignment (temperature logging, seals, paperwork, handover, "
    "escalation). Use general operations knowledge only; no tools are needed."
)
P0_KB_GOAL = (
    "Using only our knowledge base, answer and cite the source document for each: "
    "(1) When is the Project Halcyon database migration window? "
    "(2) On what date does the Zephyrine Analytics contract auto-renew? "
    "(3) In which city does Quokka Pay launch first?"
)
P0_KB_STEP = (
    "Step 1: The Project Halcyon database migration window is Saturday 14 November 2026"
)
P0_POLICY_GOAL = (
    "Use web search to find the current repo rate set by the Reserve Bank of India and "
    "report it with its source."
)
P0_POLICY_STEP = (
    "Step 1: Perform a web search for the current repo rate set by the Reserve Bank of India."
)
P0_HIGH_RISK_GOAL = (
    "Demo list (in-memory sample data, no external systems): rec-201 env=staging "
    "last_used=2025-02-01; rec-202 env=production last_used=2026-09-30; rec-203 env=staging "
    "last_used=2025-03-09. Delete the stale staging records (last used before 2026) from "
    "the demo list and report which record IDs were deleted."
)

FALSE_POSITIVES = [
    P0_MOA_GOAL,
    P0_KB_GOAL,
    P0_KB_STEP,
    P0_POLICY_GOAL,
    P0_POLICY_STEP,
    # same families, other phrasings
    "When is the next deployment window for the billing service?",
    "Summarize the database migration plan for Q3",
    "What is the payment schedule in the vendor contract?",
    "Which city does Quokka Pay launch in?",
    "Read the Apple Pay integration guide",
    "Find the interest rate set by the central bank last month",
    "Report the exchange rate set by the regulator",
    "Run a search for recent repo rate decisions",
    "Execute a query to count the open tickets",
    "Draft a checklist for releasing a shipment at the dock",
    "Explain the release of the cargo at customs",
    "Look up the database backup schedule",
]


@pytest.mark.parametrize("text", FALSE_POSITIVES)
def test_read_only_texts_are_not_high_risk(text: str) -> None:
    verdict = assess_step_risk(text)
    assert not verdict.high_risk, f"{text!r} flagged: {verdict.reasons}"


@pytest.mark.parametrize(
    ("step", "goal"),
    [
        (P0_KB_STEP, P0_KB_GOAL),
        (P0_POLICY_STEP, P0_POLICY_GOAL),
        ("Step 1: Outline the checklist items", P0_MOA_GOAL),
    ],
)
def test_p0_steps_of_p0_goals_run_ungated(step: str, goal: str) -> None:
    verdict = assess_step_risk(step, goal=goal)
    assert not verdict.high_risk, verdict.reasons


TRUE_POSITIVES = [
    P0_HIGH_RISK_GOAL,
    "Delete the stale staging records",
    "deploy the api to production",
    "Release v2.3 to customers",
    "publish the package to PyPI",
    "drop the legacy index",
    "DROP TABLE sessions;",
    "transfer funds to the vendor account",
    "Transfer $500 to the vendor",
    "pay the outstanding invoice",
    "Pay the contractor today",
    "Please pay the supplier",
    "migrate the customer database",
    "Run the database migration now",
    "Perform the migration of the customer database",
    "Execute the deployment to production",
    "update the user records",
    "set the database password to the new value",
    "Set the repository to private",
    "The records will be deleted by the cleanup job; run it",
    "release the funds to the supplier",
    "refund the customer's payment",
    "grant admin access to bob",
    "email the customer list to the vendor",
    "Remove the payment webhook",
    "Wire the payment to the supplier",
]


@pytest.mark.parametrize("text", TRUE_POSITIVES)
def test_true_positives_stay_high_risk(text: str) -> None:
    verdict = assess_step_risk(text)
    assert verdict.high_risk, f"{text!r} must need approval"
    assert is_high_risk_text(text)


def test_proper_name_rule_does_not_hide_title_case_actions() -> None:
    # Everything title-cased (a heading-style step) still reads the verbs.
    assert assess_step_risk("Transfer Funds To The Vendor Account").high_risk
    assert assess_step_risk("Delete Stale Records").high_risk
    assert assess_step_risk("Prepare And Send Payment To Supplier").high_risk
