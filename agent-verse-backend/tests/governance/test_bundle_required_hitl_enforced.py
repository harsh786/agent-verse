"""TRUST-02: a compliance bundle's ``required_hitl_for`` tools need a human.

``requires_hitl_for_tool_on`` had no caller: enabling e.g. GDPR (which lists
``export_user_data``) changed nothing at execution time. Both tool gates (the
AgentGraph executor and the workflow GovernedToolGate) now require an approval
for those tools, and fail closed when the tenant's bundles cannot be read.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.governance import compliance_bundles, policy_rules
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-bundle-hitl", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.fixture(autouse=True)
def _no_policy_rules(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def _none(db: Any, tenant_id: str) -> list[Any]:
        return []

    monkeypatch.setattr(policy_rules, "load_active_policy_rules", _none)
    compliance_bundles.invalidate_active_bundles()
    yield
    compliance_bundles.invalidate_active_bundles()


@pytest.fixture
def bundles(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    active: list[str] = []

    async def _active(self: Any, tenant_id: str) -> tuple[str, ...]:
        return tuple(active)

    monkeypatch.setattr(
        compliance_bundles.PostgresComplianceBundleStore, "active_bundle_ids", _active
    )
    return active


async def test_helper_names_the_bundle_that_requires_hitl(bundles: list[str]) -> None:
    bundles.append("gdpr")
    assert (
        await compliance_bundles.bundle_hitl_requirement(object(), T.tenant_id, "export_user_data")
        == "gdpr"
    )
    assert (
        await compliance_bundles.bundle_hitl_requirement(object(), T.tenant_id, "read_file") is None
    )


async def test_workflow_gate_routes_bundle_tools_to_hitl(bundles: list[str]) -> None:
    from app.agent.tool_gate import GovernedToolGate

    bundles.append("gdpr")
    gate = GovernedToolGate(db_session_factory=object(), guardrails=None)
    decision = await gate.authorize(
        tool_name="export_user_data", arguments={}, tenant_ctx=T, goal_id="g"
    )
    assert not decision.allowed and "approval" in decision.reason
    assert (
        await gate.authorize(tool_name="read_file", arguments={}, tenant_ctx=T, goal_id="g")
    ).allowed


async def test_executor_requires_approval_for_bundle_tools(bundles: list[str]) -> None:
    from app.agent.nodes.executor_mixin import ExecutorMixin

    bundles.append("gdpr")
    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._db_session_factory = object()
    ex._policy_engine = None
    ex._agent_id = "a1"
    ex._hitl_gateway = None
    ex._autonomy_mode = "supervised"
    denial = await ex._tool_policy_gate(
        tool_name="export_user_data",
        step="export the user's data",
        state=SimpleNamespace(goal_id="g1", context={}),
        tenant_ctx=T,
        arguments={},
    )
    assert denial is not None and "gdpr" in denial


async def test_unreadable_bundles_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _down(self: Any, tenant_id: str) -> tuple[str, ...]:
        raise ConnectionError("db down")

    monkeypatch.setattr(
        compliance_bundles.PostgresComplianceBundleStore, "active_bundle_ids", _down
    )
    with pytest.raises(ConnectionError):
        await compliance_bundles.bundle_hitl_requirement(object(), T.tenant_id, "read_file")
