"""QA-10: a policy's action is one the engine enforces — never a silent no-op.

``CreatePolicyRequest.action`` was free text and create only handled ``deny`` /
``require_approval``: ``"block"`` (or a typo) answered 201 for a policy that
restricted nothing. The action is now validated (``block`` is accepted as an
alias of ``deny``, anything else is a 422), and stored rows with an unknown
action — reloaded by every replica, or restored by a rollback — are enforced
as ``deny`` (fail closed) with a warning.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.governance.policies import PolicyEngine, PolicyResult, normalize_policy_action
from tests.api.test_governance_comprehensive2 import _CTX, _VALID_KEY, _make_app
from tests.governance.test_policy_reload_time_windows import _Session
from tests.governance.test_policy_versions_wired import _app, _DB

_H = {"X-API-Key": _VALID_KEY}


@pytest.mark.parametrize("action", ["block", "Block", " deny "])
def test_block_is_an_alias_of_deny(action: str) -> None:
    engine = PolicyEngine()
    client = TestClient(_make_app(policy_engine=engine))
    resp = client.post(
        "/governance/policies",
        json={"name": "no-deploy", "tools_pattern": "deploy*", "action": action},
        headers=_H,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["action"] == "deny"
    assert engine.evaluate("deploy_prod", tenant_ctx=_CTX) == PolicyResult.DENY


def test_require_approval_is_accepted() -> None:
    engine = PolicyEngine()
    client = TestClient(_make_app(policy_engine=engine))
    resp = client.post(
        "/governance/policies",
        json={"name": "ask", "tools_pattern": "deploy*", "action": "require_approval"},
        headers=_H,
    )
    assert resp.status_code == 201, resp.text
    assert engine.evaluate("deploy_prod", tenant_ctx=_CTX) == PolicyResult.REQUIRE_APPROVAL


@pytest.mark.parametrize("action", ["bogus", "allow", "", "denyy"])
def test_unknown_action_is_rejected(action: str) -> None:
    engine = PolicyEngine()
    client = TestClient(_make_app(policy_engine=engine))
    resp = client.post(
        "/governance/policies",
        json={"name": "p", "tools_pattern": "deploy*", "action": action},
        headers=_H,
    )
    assert resp.status_code == 422, resp.text
    assert engine._policies == []
    assert client.get("/governance/policies", headers=_H).json() == []


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("deny", "deny"),
        ("require_approval", "require_approval"),
        ("block", "deny"),
        ("bogus", "deny"),
        (None, "deny"),
    ],
)
def test_normalize_policy_action_fails_closed(stored: str | None, expected: str) -> None:
    assert normalize_policy_action(stored) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [True, False])
async def test_reload_enforces_an_unknown_stored_action_as_deny(strict: bool) -> None:
    session = _Session(
        {"FROM governance_policies": [("legacy", "block", "deploy*", _CTX.tenant_id, 0)]}
    )
    engine = PolicyEngine()
    if strict:
        await engine.reload_from_db(lambda: session, tenant_id=_CTX.tenant_id, strict=True)
    else:
        await engine.reload_from_db(lambda: session)
    assert engine.evaluate("deploy_prod", tenant_ctx=_CTX) == PolicyResult.DENY
    (policy,) = [p for p in engine._policies if p.name == "legacy"]
    assert policy.action == "deny"


@pytest.mark.asyncio
async def test_rollback_of_a_snapshot_with_an_unknown_action_restores_a_deny() -> None:
    rules = [{"tools_pattern": "delete_*", "action": "bogus", "priority": 0}]
    db = _DB(target=("v1", "no-delete", "", rules, 1, None))
    db.policies_rows = [("no-delete", "bogus", "delete_*", "t-gov", 0)]
    app = _app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/governance/policies/p1/rollback", json={"target_version": 1, "reason": "r"}
        )
    assert r.status_code == 200, r.text
    (upsert,) = db.sql("INSERT INTO governance_policies")
    assert upsert[1]["action"] == "deny"
    assert app.state._policy_registry["t-gov"]["p1"]["action"] == "deny"
