"""a03-F058-02: governance admin mutations write an admin audit event.

``audit_admin_action`` existed only at its definition; no route applied it.
Applied to a real route it would also have raised in its ``finally`` (it read
``tenant.id`` / ``request.state.api_key``, which a TenantContext lacks).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.governance import router as gov_router
from app.api.trust_governance import router as trust_router
from app.governance.audit_v2 import AuditEvent
from app.governance.policies import PolicyEngine
from tests.governance._router_app import make_app, tenant


class _Writer:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self.fail = fail

    async def write(self, event: AuditEvent) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.events.append(event)


def _client(writer: Any) -> TestClient:
    app = make_app(gov_router, trust_router, ctx=tenant("t-adm-audit"))
    app.state.policy_engine = PolicyEngine()
    app.state.audit_writer = writer
    return TestClient(app, raise_server_exceptions=False)


def test_policy_create_and_failed_delete_are_audited() -> None:
    writer = _Writer()
    client = _client(writer)
    r = client.post(
        "/governance/policies",
        json={"name": "no-deploys", "tools_pattern": "deploy*", "action": "deny"},
    )
    assert r.status_code == 201, r.text
    missing = client.delete("/governance/policies/does-not-exist")
    assert missing.status_code == 404

    created, failed = writer.events
    assert created.event_type == "policy.created"
    assert created.status == "success"
    assert created.tenant_id == "t-adm-audit"
    assert created.api_key_id == "k-gov"
    assert created.resource_id == "no-deploys"
    assert failed.event_type == "policy.deleted"
    assert failed.status == "failure"
    assert failed.error_code == "HTTP_404"
    assert failed.resource_id == "does-not-exist"


def test_compliance_bundle_enable_is_audited() -> None:
    writer = _Writer()
    client = _client(writer)
    assert client.post("/trust/compliance-bundles/hipaa/enable").status_code == 200
    assert client.delete("/trust/compliance-bundles/hipaa").status_code == 200
    assert [(e.event_type, e.resource_id) for e in writer.events] == [
        ("compliance_bundle.enabled", "hipaa"),
        ("compliance_bundle.disabled", "hipaa"),
    ]


def test_an_audit_write_failure_never_changes_the_answer() -> None:
    client = _client(_Writer(fail=True))
    r = client.post(
        "/governance/policies",
        json={"name": "p", "tools_pattern": "x*", "action": "deny"},
    )
    assert r.status_code == 201, r.text
