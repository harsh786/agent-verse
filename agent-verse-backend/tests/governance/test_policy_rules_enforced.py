"""POL-01: policy-as-code rules are enforced on execution paths.

``/governance/policy-rules`` stored rules and dry-ran them, but no execution path
(AgentGraph executor, workflow GovernedToolGate) ever read them.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.governance import policy_rules
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-polrules", plan=PlanTier.ENTERPRISE, api_key_id="k")
RULE = {
    "name": "block-external-email",
    "conditions": [
        {"field": "tool_name", "op": "contains", "value": "send_email"},
        {"field": "arguments.to", "op": "not_ends_with", "value": "@company.com"},
    ],
    "logic": "AND",
    "action": "deny",
    "message": "Emails only to @company.com",
}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    policy_rules.invalidate_policy_rules()
    yield
    policy_rules.invalidate_policy_rules()


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []

    async def _load(db: Any, tenant_id: str) -> list[dict[str, Any]]:
        return rules

    monkeypatch.setattr(policy_rules, "load_active_policy_rules", _load)
    return rules


async def test_workflow_gate_denies_by_policy_rule(stored: list[Any]) -> None:
    from app.agent.tool_gate import GovernedToolGate

    stored.append(RULE)
    gate = GovernedToolGate(db_session_factory=object(), guardrails=None)
    denied = await gate.authorize(
        tool_name="gmail_send_email", arguments={"to": "x@evil.com"}, tenant_ctx=T, goal_id="g"
    )
    assert not denied.allowed and "block-external-email" in denied.reason
    internal = await gate.authorize(
        tool_name="gmail_send_email", arguments={"to": "a@company.com"}, tenant_ctx=T, goal_id="g"
    )
    # Not denied by the rule (send_email is write_high, so it still needs approval).
    assert "block-external-email" not in internal.reason


async def test_agent_graph_executor_denies_by_policy_rule(stored: list[Any]) -> None:
    from app.agent.nodes.executor_mixin import ExecutorMixin

    stored.append(RULE)
    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._db_session_factory = object()
    ex._policy_engine = None
    ex._agent_id = "a1"
    denial = await ex._tool_policy_gate(
        tool_name="gmail_send_email",
        step="mail the report",
        state=SimpleNamespace(goal_id="g1", context={}),
        tenant_ctx=T,
        arguments={"to": "x@evil.com"},
    )
    assert denial is not None and "block-external-email" in denial


async def test_unloadable_rules_fail_closed() -> None:
    class _Down:
        async def __aenter__(self) -> Any:
            raise ConnectionError("db down")

        async def __aexit__(self, *a: object) -> None:
            return None

    denial = await policy_rules.policy_rules_denial(
        lambda: _Down(), T.tenant_id, {"tool_name": "read_file"}
    )
    assert denial is not None and "failing closed" in denial


@pytest.mark.integration
async def test_rule_written_by_the_api_binds_under_the_app_role(pg_url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.memory._pg import app_role_engine, sessionmaker_for

    tenant = f"t-pr-{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO policy_rules (id, tenant_id, name, rule_json, is_active) "
                "VALUES (:id, :t, 'r', CAST(:rule AS json), true)"
            ),
            {"id": uuid.uuid4().hex, "t": tenant, "rule": json.dumps(RULE)},
        )
    await admin.dispose()
    engine = await app_role_engine(pg_url, ["policy_rules"])
    try:
        factory = sessionmaker_for(engine)
        ctx = {"tool_name": "send_email", "arguments": {"to": "x@evil.com"}}
        assert await policy_rules.policy_rules_denial(factory, tenant, ctx) is not None
        assert await policy_rules.policy_rules_denial(factory, "other-tenant", ctx) is None
    finally:
        await engine.dispose()
