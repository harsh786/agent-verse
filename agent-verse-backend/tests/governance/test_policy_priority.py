"""QA-9: policy priority decides which matching policy applies.

``Policy`` had no priority and ``evaluate`` returned the first match in list
order, so the outcome for a tool matched by both a deny and a require_approval
policy depended on which was created (or reloaded) first. Matching policies are
now ranked by priority (highest first); ties go to the most restrictive action
(deny > require_approval > allow). Reloads read the persisted priority.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.tenancy.context import PlanTier, TenantContext
from tests.governance.test_policy_reload_time_windows import _Session

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _deny(priority: int) -> Policy:
    return Policy(name="deny", denied_tools=["deploy*"], tenant_id="t1", priority=priority)


def _ask(priority: int) -> Policy:
    return Policy(name="ask", approval_tools=["deploy*"], tenant_id="t1", priority=priority)


@pytest.mark.parametrize(
    ("policies", "expected"),
    [
        ([_deny(0), _ask(10)], PolicyResult.REQUIRE_APPROVAL),
        ([_deny(10), _ask(0)], PolicyResult.DENY),
        ([_deny(5), _ask(5)], PolicyResult.DENY),
    ],
    ids=["approval-outranks", "deny-outranks", "tie-deny-wins"],
)
@pytest.mark.parametrize("reverse", [False, True], ids=["created-first", "created-last"])
def test_creation_order_does_not_change_the_result(
    policies: list[Policy], expected: PolicyResult, reverse: bool
) -> None:
    engine = PolicyEngine(list(reversed(policies)) if reverse else list(policies))
    assert engine.evaluate("deploy_prod", tenant_ctx=CTX) == expected


def test_same_policy_tie_prefers_deny() -> None:
    engine = PolicyEngine(
        [Policy(name="both", denied_tools=["deploy*"], approval_tools=["deploy*"], tenant_id="t1")]
    )
    assert engine.evaluate("deploy_prod", tenant_ctx=CTX) == PolicyResult.DENY


def test_non_matching_higher_priority_policy_is_ignored() -> None:
    engine = PolicyEngine(
        [
            Policy(name="other", approval_tools=["slack_*"], tenant_id="t1", priority=99),
            _deny(0),
        ]
    )
    assert engine.evaluate("deploy_prod", tenant_ctx=CTX) == PolicyResult.DENY


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("reverse", [False, True])
async def test_reload_keeps_the_persisted_priority(strict: bool, reverse: bool) -> None:
    rows: list[Any] = [
        ("deny-deploys", "deny", "deploy*", "t1", 0),
        ("ask-deploys", "require_approval", "deploy*", "t1", 10),
    ]
    session = _Session({"FROM governance_policies": rows[::-1] if reverse else rows})
    engine = PolicyEngine()
    if strict:
        await engine.reload_from_db(lambda: session, tenant_id="t1", strict=True)
    else:
        await engine.reload_from_db(lambda: session)
    assert {p.name: p.priority for p in engine._policies if p.tenant_id} == {
        "deny-deploys": 0,
        "ask-deploys": 10,
    }
    assert engine.evaluate("deploy_prod", tenant_ctx=CTX) == PolicyResult.REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_reload_selects_priority() -> None:
    seen: list[str] = []

    class _Recording(_Session):
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            seen.append(str(stmt))
            return await super().execute(stmt, params)

    engine = PolicyEngine()
    await engine.reload_from_db(lambda: _Recording({}), tenant_id="t1", strict=True)
    await engine.reload_from_db(lambda: _Recording({}), tenant_id="t1")
    selects = [s for s in seen if "FROM governance_policies" in s]
    assert len(selects) == 2
    assert all("priority" in s for s in selects)
