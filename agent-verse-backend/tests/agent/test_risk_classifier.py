"""HIGH-RISK-GATE-WORDING (RW-20): the step approval gate must not depend on the
exact words a planner picks.

A real run planned "Step 3: Remove those identified stale staging records from
the list." for a goal that asked to delete them; the keyword gate only knew
"delete" and the step ran without approval. These tables pin the classifier on
many phrasings of the same intents (and on benign steps that must stay ungated).
"""

from __future__ import annotations

import pytest

from app.agent.nodes._helpers import _is_high_risk_step
from app.agent.risk_classifier import (
    HIGH_RISK_VOCABULARY,
    analyze_text,
    assess_step_risk,
    normalize_words,
)

RW20_GOAL = (
    "Demo list (in-memory sample data, no external systems or tools needed): rec-101 "
    "env=staging last_used=2025-01-03; rec-102 env=production last_used=2026-09-28. "
    "Delete the stale staging records (env=staging and last_used before 2026) from the "
    "demo list and report exactly which record IDs were removed and which remain."
)

# ── destructive intent, many phrasings ──────────────────────────────────────

DESTRUCTIVE_STEPS = [
    "Step 3: Remove those identified stale staging records from the list.",
    "remove the stale records",
    "Removing the expired entries",
    "Removal of old user accounts",
    "purge the audit table",
    "Purging cached sessions",
    "wipe the staging bucket",
    "erase all backups older than a year",
    "Erasure of customer PII",
    "destroy the test cluster",
    "truncate the events table",
    "drop the legacy index",
    "Delete the stale staging records",
    "deleting rows older than 30 days",
    "revoke the contractor's access",
    "Revocation of API keys",
    "terminate the idle instances",
    "kill the runaway job",
    "nuke the old environment",
    "decommission the reporting server",
    "deprovision the sandbox accounts",
    "uninstall the agent from all hosts",
    "deactivate the dormant users",
    "disable the payment webhook",
    "cancel the pending orders",
    "overwrite the config with defaults",
    "reset the admin password",
    "flush the redis cache",
    "prune unused docker images",
    "clean up the stale branches",
    "cleanup old artifacts",
    "tear down the preview environment",
    "get rid of the duplicate customer rows",
    "unpublish the draft article",
    "suspend the abusive account",
    "ban the spammer",
    "bulk-delete the archived tickets",
    "massdelete inactive users",
    "call github_delete_repo for the fork",
    "run deleteUser on the stale account",
    "invoke purgeQueue",
]

# ── release / deploy intent ─────────────────────────────────────────────────

RELEASE_STEPS = [
    "deploy the api",
    "Deployment of the new build",
    "redeploy the worker",
    "release v2.3 to customers",
    "publish the package to PyPI",
    "Publishing the blog post",
    "roll out the feature flag to everyone",
    "go live with the new pricing page",
    "push to the main branch",
    "force push the rebased branch",
]

# ── money ───────────────────────────────────────────────────────────────────

FINANCIAL_STEPS = [
    "transfer $500 to the vendor",
    "Transferring funds to the escrow account",
    "pay the outstanding invoice",
    "Paid the contractor",
    "issue a refund to the customer",
    "refund order #1234",
    "charge the customer's card",
    "purchase 10 licences",
    "buy 100 shares of ACME",
    "sell the bitcoin position",
    "withdraw the balance",
    "disburse salaries for October",
    "wire the payment to the supplier",
]

# ── privilege ───────────────────────────────────────────────────────────────

PRIVILEGE_STEPS = [
    "grant admin access to bob",
    "elevate the service account",
    "chmod 777 the uploads dir",
    "make admin the new hire",
    "add bob to the admins role",
    "update the IAM permissions for the deploy user",
]

# ── sensitive targets with a change / outbound communication ───────────────

SENSITIVE_TARGET_STEPS = [
    "restart prod",
    "update the production config",
    "check the production error rate",
    "migrate the customer database",
    "rotate the database credentials",
    "update the user records",
    "change the billing plan",
    "modify the firewall rules",
    "move the backups to cold storage",
    "apply the schema change to the live database",
    "email the report to all customers",
    "send the customer list to the partner",
    "post the announcement on twitter",
    "share the API keys with the contractor",
    "export customer data to a CSV for the vendor",
    "notify all users about the outage",
    "Customer payroll for October",  # ambiguous: sensitive target, no readable action
    "update the customer's billing address",
    "email the customer list to the vendor",
]

# ── destructive commands ────────────────────────────────────────────────────

COMMAND_STEPS = [
    "rm -rf build/",
    "run rm -rf /tmp/data",
    "execute DROP TABLE sessions;",
    "DELETE FROM orders WHERE created_at < now() - interval '1 year'",
    "UPDATE accounts SET balance = 0",
    "kubectl delete namespace staging",
    "terraform destroy -auto-approve",
    "git push --force origin main",
    "git reset --hard HEAD~3",
    "redis-cli FLUSHALL",
    "aws s3 rm s3://bucket --recursive",
]

# ── obfuscated / unusual formatting ─────────────────────────────────────────

OBFUSCATED_STEPS = [
    "DEPLOY to staging",
    "Ｒｅｍｏｖｅ the records",  # fullwidth letters (NFKC)
    "re​move the records",  # zero-width space
    "de­lete the rows",  # soft hyphen
    "REMOVE_STALE_RECORDS",
    "removeStaleRecords()",
    "Delete-the-records",
    "Do NOT delete anything yet, just delete the temp rows",
]


@pytest.mark.parametrize(
    "step",
    DESTRUCTIVE_STEPS
    + RELEASE_STEPS
    + FINANCIAL_STEPS
    + PRIVILEGE_STEPS
    + SENSITIVE_TARGET_STEPS
    + COMMAND_STEPS
    + OBFUSCATED_STEPS,
)
def test_high_risk_phrasings_need_approval(step: str) -> None:
    verdict = assess_step_risk(step)
    assert verdict.high_risk, f"{step!r} must need approval"
    assert verdict.reasons, "a high-risk verdict must say why"
    # The legacy helper the gates call agrees.
    assert _is_high_risk_step(step)


# ── benign steps stay ungated (outside a high-risk goal) ────────────────────

BENIGN_STEPS = [
    "summarize the product reviews",
    "list all products in the catalogue",
    "measure team productivity",
    "open the dropdown menu",
    "format the document and perform analysis",
    "fetch user profile from API",
    "Search the web for the latest LLM benchmarks",
    "Summarize the latest release notes of the repository",
    "Explain the role of mitochondria",
    "Find the root cause of the failing test",
    "Read the newly released model card",
    "Compute the average of the numbers",
    "Translate the paragraph into French",
    "Write a short poem about the sea",
    "Draft a reply to the question",
    "Send the summary to the user",
    "Identify which records are stale",
    "Compare the two proposals and recommend one",
    "Pay attention to edge cases while analysing the data",
    "Explain transfer learning",
    "List customer records created this week",
    "Navigate to example.com and read the headline",
    "Launch a browser and open the docs page",
    "Summarize the grant proposal",
    "Calculate shipping cost for the order",
    "Answer the user's question about the weather",
    "email the customer",  # one recipient: the dispatch-time tool gate governs the send
    "tenant A task",
    "Record the result",
]


@pytest.mark.parametrize("step", BENIGN_STEPS)
def test_benign_steps_are_not_gated(step: str) -> None:
    verdict = assess_step_risk(step)
    assert not verdict.high_risk, f"{step!r} flagged: {verdict.reasons}"


# ── the goal's intent covers rephrased / vague steps ────────────────────────


@pytest.mark.parametrize(
    ("step", "risky_on_its_own"),
    [
        ("Step 3: Apply the cleanup to those records.", True),  # cleanup = destructive
        ("Step 3: Process the identified records accordingly.", True),  # change to records
        ("Step 3: Update the list so only fresh records remain.", True),
        ("Step 3: Handle the stale entries.", False),  # no recognisable verb: ambiguous
        ("Step 3: Finalise the stale entries.", False),
        ("Execute the goal autonomously", False),
    ],
)
def test_vague_step_of_a_destructive_goal_needs_approval(
    step: str, risky_on_its_own: bool
) -> None:
    verdict = assess_step_risk(step, goal=RW20_GOAL)
    assert verdict.high_risk, verdict
    assert any("high-risk goal" in r for r in verdict.reasons)
    # Under a benign goal only the steps that are risky by themselves are gated.
    benign = assess_step_risk(step, goal="Summarize the demo list of entries")
    assert benign.high_risk is risky_on_its_own, benign


@pytest.mark.parametrize(
    "step",
    [
        "Step 1: Parse the demo list into individual records with ID, env, and last_used.",
        "Step 2: Identify records where env equals staging and last_used is before 2026.",
        "Summarize which records match the criteria",
    ],
)
def test_read_only_steps_of_a_destructive_goal_run_ungated(step: str) -> None:
    assert not assess_step_risk(step, goal=RW20_GOAL).high_risk


def test_rw20_exact_step_is_gated_with_and_without_the_goal() -> None:
    step = "Step 3: Remove those identified stale staging records from the list."
    assert assess_step_risk(step).high_risk
    verdict = assess_step_risk(step, goal=RW20_GOAL)
    assert verdict.high_risk
    assert any("remove" in r for r in verdict.reasons)


# ── tool risk metadata ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "high"),
    [
        ("github_delete_repo", True),
        ("jira_close_issue", True),  # Jira close is destructive in tool_risk
        ("stripe_create_refund", True),
        ("slack_send_message", False),  # governed by the tool gate at dispatch
        ("web_search", False),
        ("llm_call", False),
        ("", False),
    ],
)
def test_tool_metadata_contributes(tool: str, high: bool) -> None:
    verdict = assess_step_risk("Use the tool for this step", tool_name=tool)
    assert verdict.high_risk is high, verdict


# ── building blocks ─────────────────────────────────────────────────────────


def test_normalisation_splits_identifiers_and_strips_invisibles() -> None:
    assert normalize_words("deleteUser") == ["delete", "user"]
    assert normalize_words("github_delete-repo") == ["github", "delete", "repo"]
    assert normalize_words("re​move") == ["remove"]
    assert normalize_words("ＤＲＯＰ") == ["drop"]


def test_inflections_reduce_to_one_lemma() -> None:
    for word in ("remove", "removes", "removed", "removing", "removal"):
        assert ("remove", "destructive") in {(v, c.value) for v, c in analyze_text(word).verbs}


def test_vocabulary_keeps_the_original_keywords() -> None:
    for word in ("deploy", "delete", "drop", "prod", "production", "destroy", "wipe",
                 "truncate", "remove", "purge", "erase", "revoke", "terminate",
                 "release", "publish", "transfer", "pay"):
        assert word in HIGH_RISK_VOCABULARY


def test_empty_step_is_not_high_risk() -> None:
    assert not assess_step_risk("").high_risk
    assert not assess_step_risk("   ", goal=RW20_GOAL).high_risk
