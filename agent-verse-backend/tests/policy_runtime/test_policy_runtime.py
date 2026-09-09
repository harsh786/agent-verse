"""RuntimeEnforcer — P0 security gates.

Policy-compilation coverage (formerly against the orphan ``PolicyCompiler``)
now lives against its live replacement, ``PolicyBundleSelector``
(see ``tests/security_runtime/``).
"""

from __future__ import annotations

from app.policy_runtime.constraint_model import RuntimeConstraints
from app.policy_runtime.runtime_enforcer import RuntimeEnforcer


def test_enforcer_denies_unlisted_capability() -> None:
    constraints = RuntimeConstraints(
        allowed_capabilities=[],
        denied_capabilities=[],
        required_approvals=[],
        max_cost_usd=10.0,
        audit_level="standard",
    )
    e = RuntimeEnforcer()
    assert e.is_capability_allowed("tool:web_search", constraints) is False


def test_enforcer_blocks_denied_capability() -> None:
    constraints = RuntimeConstraints(
        allowed_capabilities=[],
        denied_capabilities=["tool:shell"],
        required_approvals=[],
        max_cost_usd=10.0,
        audit_level="standard",
    )
    e = RuntimeEnforcer()
    assert e.is_capability_allowed("tool:shell", constraints) is False
    assert e.check_cost(5.0, constraints) is True
    assert e.check_cost(15.0, constraints) is False


def test_policy_trace_records_decisions():
    from app.policy_runtime.policy_trace import PolicyTrace

    trace = PolicyTrace(goal_id="g1", tenant_id="t1")
    trace.record("audit_level", "forensic", "risk=critical", source="risk_level")
    assert len(trace.decisions) == 1
    import json

    json.dumps(trace.to_dict())
