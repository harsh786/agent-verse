"""GRANTS-NAMESPACED: grants, tool policies, the permission matrix and per-agent
permissions match connection-namespaced tool names correctly.

Two MongoDB connections each expose ``mongodb_find``; the model is offered
``orders_db__mongodb_find`` and ``analytics_db__mongodb_find``. Governance was
written against bare names only, so a correct bare grant DENIED the qualified
call, and a rule could not target one connection. Now:

* a rule naming the bare tool covers it on every connection the agent uses;
* a rule naming ``<slug>__<tool>`` or ``<connector id>/<tool>`` covers ONLY that
  connection — never the same tool on another one (no accidental allow).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agent.tool_context import ToolRef
from app.governance.agent_permissions import AgentPermissionRule, match_rule, resolve_level
from app.governance.grants import check_grant
from app.governance.grants.models import Grant
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.mcp.tool_naming import qualify_colliding_tools
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-gov", plan=PlanTier.PROFESSIONAL, api_key_id="k")
ORDERS_ID = "builtin-mongodb:orders-db"
ANALYTICS_ID = "builtin-mongodb:analytics-db"


def _tools() -> dict[str, ToolRef]:
    refs = qualify_colliding_tools(
        [
            ToolRef(ORDERS_ID, "orders-db", "mongodb_find", "", {}),
            ToolRef(ANALYTICS_ID, "analytics-db", "mongodb_find", "", {}),
            ToolRef(ORDERS_ID, "orders-db", "mongodb_delete_one", "", {}),
            ToolRef(ANALYTICS_ID, "analytics-db", "mongodb_delete_one", "", {}),
            ToolRef("builtin-redis:cache", "cache", "redis_get", "", {}),
        ]
    )
    return {str(t.name): t for t in refs}


@pytest.fixture
def tools() -> dict[str, ToolRef]:
    return _tools()


def test_names_are_qualified_only_on_collision(tools: dict[str, ToolRef]) -> None:
    assert set(tools) == {
        "orders_db__mongodb_find",
        "analytics_db__mongodb_find",
        "orders_db__mongodb_delete_one",
        "analytics_db__mongodb_delete_one",
        "redis_get",
    }


# ── grants ───────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, *scopes: str) -> None:
        now = datetime.now(UTC)
        self.grant = Grant(
            grant_id="g1",
            tenant_id="t-gov",
            grantor="owner",
            grantee_agent_id="agent-1",
            scopes=tuple(scopes),
            not_before=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> list[Grant]:
        return [self.grant]


async def _allowed(store: _Store, name: Any) -> bool:
    decision = await check_grant(store, tenant_id="t-gov", agent_id="agent-1", tool_name=name)
    return decision.allowed


@pytest.mark.parametrize(
    ("scope", "orders", "analytics", "redis"),
    [
        ("mongodb_find", True, True, False),  # bare: every connection
        ("orders_db__mongodb_find", True, False, False),  # one connection by slug
        (f"{ORDERS_ID}/mongodb_find", True, False, False),  # one connection by id
        (f"{ANALYTICS_ID}/*", False, True, False),  # every tool on one connection
        ("orders_db__*", True, False, False),
        ("redis_get", False, False, True),  # a non-colliding tool, bare
        ("cache__redis_get", False, False, True),  # ...or targeted at its connection
        ("analytics_db__redis_get", False, False, False),  # wrong connection
    ],
)
async def test_grant_scopes(
    tools: dict[str, ToolRef], scope: str, orders: bool, analytics: bool, redis: bool
) -> None:
    store = _Store(scope)
    assert await _allowed(store, tools["orders_db__mongodb_find"].name) is orders
    assert await _allowed(store, tools["analytics_db__mongodb_find"].name) is analytics
    assert await _allowed(store, tools["redis_get"].name) is redis


async def test_plain_string_names_keep_exact_matching() -> None:
    store = _Store("mongodb_find")
    assert await _allowed(store, "mongodb_find")
    # A bare string carries no connection identity: no accidental allow.
    assert not await _allowed(store, "orders_db__mongodb_find")


def test_toolref_rebuilt_from_state_keeps_its_governance_forms(
    tools: dict[str, ToolRef],
) -> None:
    ref = tools["analytics_db__mongodb_find"]
    rebuilt = ToolRef(**{**ref.__dict__, "name": str(ref.name)})
    assert Grant.covers(_Store("mongodb_find").grant, rebuilt.name, datetime.now(UTC))
    assert not Grant.covers(_Store("orders_db__*").grant, rebuilt.name, datetime.now(UTC))


# ── tool policies ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("denied", "orders", "analytics"),
    [
        ("mongodb_delete_one", PolicyResult.DENY, PolicyResult.DENY),
        ("orders_db__*", PolicyResult.DENY, PolicyResult.ALLOW),
        (f"{ANALYTICS_ID}/mongodb_delete_one", PolicyResult.ALLOW, PolicyResult.DENY),
    ],
)
def test_policy_deny_patterns(
    tools: dict[str, ToolRef], denied: str, orders: PolicyResult, analytics: PolicyResult
) -> None:
    engine = PolicyEngine([Policy(name="p", denied_tools=[denied])])
    assert engine.evaluate(tools["orders_db__mongodb_delete_one"].name, tenant_ctx=CTX) == orders
    assert (
        engine.evaluate(tools["analytics_db__mongodb_delete_one"].name, tenant_ctx=CTX) == analytics
    )


def test_policy_approval_on_bare_tool_applies_to_every_connection(
    tools: dict[str, ToolRef],
) -> None:
    engine = PolicyEngine([Policy(name="p", approval_tools=["mongodb_find"])])
    for name in ("orders_db__mongodb_find", "analytics_db__mongodb_find"):
        assert engine.evaluate(tools[name].name, tenant_ctx=CTX) == PolicyResult.REQUIRE_APPROVAL


# ── permission matrix ────────────────────────────────────────────────────────


def test_matrix_default_deny_and_connection_specific_allow(tools: dict[str, ToolRef]) -> None:
    matrix = PermissionMatrix()
    matrix.set_default_rule(PermissionRule(tool_name="*delete*", level=ActionLevel.DENY))
    matrix.set_rule(
        PermissionRule(tool_name="orders_db__mongodb_delete_one", level=ActionLevel.ALLOW),
        tenant_ctx=CTX,
    )
    orders = tools["orders_db__mongodb_delete_one"].name
    analytics = tools["analytics_db__mongodb_delete_one"].name
    assert matrix.check(orders, tenant_ctx=CTX) == ActionLevel.ALLOW
    assert matrix.check(analytics, tenant_ctx=CTX) == ActionLevel.DENY


def test_matrix_bare_rule_applies_on_every_connection(tools: dict[str, ToolRef]) -> None:
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="mongodb_find", level=ActionLevel.APPROVAL), tenant_ctx=CTX
    )
    for name in ("orders_db__mongodb_find", "analytics_db__mongodb_find"):
        assert matrix.check(tools[name].name, tenant_ctx=CTX) == ActionLevel.APPROVAL


# ── per-agent permissions ────────────────────────────────────────────────────


def _rules(*pairs: tuple[str, ActionLevel]) -> tuple[AgentPermissionRule, ...]:
    return tuple(AgentPermissionRule(tool_name=t, level=lvl) for t, lvl in pairs)


def test_bare_agent_rule_covers_every_connection(tools: dict[str, ToolRef]) -> None:
    rules = _rules(("mongodb_find", ActionLevel.ALLOW))
    for name in ("orders_db__mongodb_find", "analytics_db__mongodb_find"):
        level, _rule, _ = resolve_level(rules, tools[name].name)
        assert level is ActionLevel.ALLOW


def test_broad_allow_never_overrides_a_bare_deny(tools: dict[str, ToolRef]) -> None:
    rules = _rules(("*", ActionLevel.ALLOW), ("mongodb_delete_one", ActionLevel.DENY))
    rule = match_rule(rules, tools["orders_db__mongodb_delete_one"].name)
    assert rule is not None and rule.level is ActionLevel.DENY


def test_connection_rule_targets_only_that_connection(tools: dict[str, ToolRef]) -> None:
    rules = _rules(
        ("mongodb_*", ActionLevel.DENY),
        ("orders_db__mongodb_find", ActionLevel.ALLOW),
    )
    orders = match_rule(rules, tools["orders_db__mongodb_find"].name)
    analytics = match_rule(rules, tools["analytics_db__mongodb_find"].name)
    assert orders is not None and orders.level is ActionLevel.ALLOW
    assert analytics is not None and analytics.level is ActionLevel.DENY


def test_equally_specific_globs_resolve_to_the_restrictive_level(
    tools: dict[str, ToolRef],
) -> None:
    rules = _rules(("orders_db__*", ActionLevel.ALLOW), ("*mongodb_find", ActionLevel.DENY))
    # "orders_db__" (11 literal chars) vs "mongodb_find" (12): the deny is more specific.
    rule = match_rule(rules, tools["orders_db__mongodb_find"].name)
    assert rule is not None and rule.level is ActionLevel.DENY
