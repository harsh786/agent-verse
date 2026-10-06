"""QA-11: guardrail ``quarantine`` and ``allow`` actions mean what they say.

``quarantine`` used to do nothing: the engine recorded a violation and then only
acted on BLOCK / REQUIRE_HITL / REDACT, so quarantined content flowed on. And
the legacy ``/guardrails`` API mapped ``allow`` onto ``log``, so an explicit
allow (exemption) rule recorded a violation every time it matched.

Now QUARANTINE blocks the content like BLOCK and is reported as quarantined,
and an ALLOW rule that matches takes no action and records no violation.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.guardrails_v2.engine import GuardrailsEngine
from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, GuardrailRule

TENANT = "t-qa11"


def _kw_rule(action: GuardrailAction, rule_id: str = "kw", word: str = "banana") -> GuardrailRule:
    return GuardrailRule(
        rule_id=rule_id,
        tenant_id=TENANT,
        name=f"{action.value} {word}",
        rule_type="keyword_block",
        layers=[GuardrailLayer.STEP],
        action=action,
        config={"keywords": [word]},
        severity="high",
    )


class _RecordingRepo:
    """Minimal repository: no persisted rules, records violation writes."""

    def __init__(self) -> None:
        self.recorded: list[Any] = []

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        return []

    async def record_violations(self, tenant_id: str, violations: list[Any]) -> None:
        self.recorded.extend(violations)


@pytest.mark.asyncio
async def test_quarantine_blocks_and_is_reported_as_quarantined() -> None:
    engine = GuardrailsEngine()
    engine.add_rule(_kw_rule(GuardrailAction.QUARANTINE))

    result = await engine.evaluate("a banana split", GuardrailLayer.STEP, TENANT)

    assert result["blocked"] is True
    assert result["quarantined"] is True
    assert result["redacted_content"] is None
    assert [v["action"] for v in result["violations"]] == ["quarantine"]
    assert [v.action_taken for v in engine.get_violations(TENANT)] == ["quarantine"]


@pytest.mark.asyncio
async def test_block_is_not_reported_as_quarantined() -> None:
    engine = GuardrailsEngine()
    engine.add_rule(_kw_rule(GuardrailAction.BLOCK))

    result = await engine.evaluate("a banana split", GuardrailLayer.STEP, TENANT)

    assert result["blocked"] is True
    assert result["quarantined"] is False


@pytest.mark.asyncio
async def test_allow_rule_records_no_violation_and_takes_no_action() -> None:
    engine = GuardrailsEngine()
    repo = _RecordingRepo()
    engine.bind_repository(repo)
    engine.add_rule(_kw_rule(GuardrailAction.ALLOW))

    result = await engine.evaluate("a banana split", GuardrailLayer.STEP, TENANT)

    assert result["blocked"] is False
    assert result["quarantined"] is False
    assert result["hitl_required"] is False
    assert result["violation_count"] == 0
    assert result["violations"] == []
    assert result["redacted_content"] == "a banana split"
    assert engine.get_violations(TENANT) == []
    assert repo.recorded == []


@pytest.mark.asyncio
async def test_allow_rule_does_not_suppress_other_rules() -> None:
    """No allow-list semantics exist: a matching ALLOW rule is a no-op, it does
    not exempt the content from the tenant's other rules."""
    engine = GuardrailsEngine()
    engine.add_rule(_kw_rule(GuardrailAction.ALLOW, rule_id="allow"))
    engine.add_rule(_kw_rule(GuardrailAction.BLOCK, rule_id="block"))

    result = await engine.evaluate("a banana split", GuardrailLayer.STEP, TENANT)

    assert result["blocked"] is True
    assert [v["action"] for v in result["violations"]] == ["block"]


@pytest.mark.asyncio
async def test_simulate_reports_quarantine_as_blocking_and_skips_allow() -> None:
    engine = GuardrailsEngine()
    engine.add_rule(_kw_rule(GuardrailAction.QUARANTINE, rule_id="q", word="banana"))
    engine.add_rule(_kw_rule(GuardrailAction.ALLOW, rule_id="a", word="split"))

    sim = await engine.simulate("a banana split", "step", TENANT)

    assert sim["would_block"] is True
    assert sim["would_quarantine"] is True
    assert [t["action"] for t in sim["triggered_rules"]] == ["quarantine"]

    only_allow = await engine.simulate("a lemon split", "step", TENANT)
    assert only_allow["would_block"] is False
    assert only_allow["would_quarantine"] is False
    assert only_allow["triggered_rules"] == []


def test_legacy_api_maps_allow_to_allow_not_log() -> None:
    from app.api.guardrails import _v2_rule_fields

    fields = _v2_rule_fields(
        {"name": "exempt", "rule_type": "keyword", "action": "allow", "layers": ["step"]}
    )
    assert fields["action"] == GuardrailAction.ALLOW

    quarantine = _v2_rule_fields(
        {"name": "q", "rule_type": "keyword", "action": "quarantine", "layers": ["step"]}
    )
    assert quarantine["action"] == GuardrailAction.QUARANTINE


def test_stored_legacy_allow_rule_loads_as_allow() -> None:
    """Rules created through /guardrails with action "allow" before the fix were
    stored as ``log``; their legacy record still says "allow"."""
    from app.db.models.guardrail_rule import GuardrailRuleRow
    from app.guardrails_v2.repository import _from_row

    row = GuardrailRuleRow(
        rule_id="r-old",
        tenant_id=TENANT,
        name="old exemption",
        rule_type="keyword_block",
        layers=["step"],
        action="log",
        categories=[],
        severity="high",
        enabled=True,
        config={"keywords": ["banana"], "_legacy": {"action": "allow"}},
        version=1,
    )
    assert _from_row(row).action == GuardrailAction.ALLOW

    row.config = {"keywords": ["banana"], "_legacy": {"action": "log"}}
    assert _from_row(row).action == GuardrailAction.LOG
