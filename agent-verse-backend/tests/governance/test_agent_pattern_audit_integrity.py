import json

import pytest

from app.governance.audit_v3 import AuditV3


async def test_security_event_is_hashed_and_worm_export_is_verifiable() -> None:
    audit = AuditV3()
    record = await audit.append_security_event(
        tenant_id="t1", object_id="session-1", action="bid_unseal",
        actor="user:release-owner", object_digest="sha256:object",
        version_digest="sha256:version", reason="deadline reached",
        correlation_id="corr-1", causation_id="event-1", outcome="success",
    )
    bundle = json.loads(audit.export_worm_bundle("t1"))
    assert record.entry_hash
    assert bundle["retention_lock"] == "compliance"
    assert bundle["manifest_digest"].startswith("sha256:")
    assert audit.verify_chain("t1")["valid"] is True


async def test_unknown_audit_action_and_single_party_break_glass_fail_closed() -> None:
    audit = AuditV3()
    with pytest.raises(ValueError, match="unsupported audited action"):
        await audit.append_security_event(
            tenant_id="t1", object_id="x", action="raw_prompt_dump", actor="system",
            object_digest="sha256:o", version_digest="sha256:v", reason="no",
            correlation_id="c", causation_id="p", outcome="success",
        )
    with pytest.raises(PermissionError, match="two distinct"):
        audit.authorize_break_glass(("owner", "owner"))
