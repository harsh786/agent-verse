"""QA-9: POST /governance/policies applies the requested priority to the engine.

``create_policy`` accepted ``priority`` but never passed it to the engine's
``Policy``, so the first-created of two overlapping policies always won.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.governance.policies import PolicyEngine, PolicyResult
from tests.api.test_governance_comprehensive2 import _CTX, _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}
_DENY = {"name": "deny-deploys", "tools_pattern": "deploy*", "action": "deny", "priority": 0}
_ASK = {
    "name": "ask-deploys",
    "tools_pattern": "deploy*",
    "action": "require_approval",
    "priority": 10,
}


@pytest.mark.parametrize("order", [(_DENY, _ASK), (_ASK, _DENY)], ids=["deny-first", "ask-first"])
def test_priority_decides_regardless_of_creation_order(order: tuple[dict[str, Any], ...]) -> None:
    engine = PolicyEngine()
    client = TestClient(_make_app(policy_engine=engine))
    for body in order:
        resp = client.post("/governance/policies", json=body, headers=_H)
        assert resp.status_code == 201, resp.text
        assert resp.json()["priority"] == body["priority"]

    assert engine.evaluate("deploy_prod", tenant_ctx=_CTX) == PolicyResult.REQUIRE_APPROVAL
    assert sorted(p.priority for p in engine._policies) == [0, 10]
