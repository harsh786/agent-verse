"""Tests for HITLWorkflowGateway — all 20 HITL features."""
from __future__ import annotations

import pytest

from app.workflow.hitl_extension import (
    HITLWorkflowGateway,
    WorkflowHITLRequest,
    make_context_item,
    make_number_context,
)


@pytest.fixture
def gateway() -> HITLWorkflowGateway:
    return HITLWorkflowGateway()


def _req(**kwargs) -> WorkflowHITLRequest:
    defaults = dict(
        run_id="run-1",
        workflow_id="wf-1",
        tenant_id="tenant-1",
        step_id="step-hitl",
    )
    defaults.update(kwargs)
    return WorkflowHITLRequest(**defaults)


# ── Assignment strategies ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_specific_user(gateway: HITLWorkflowGateway) -> None:
    req = _req(assignment_strategy="specific_user", assigned_to="user-123")
    created = await gateway.create_request(req)
    assert created.assigned_to == "user-123"


@pytest.mark.asyncio
async def test_assign_round_robin(gateway: HITLWorkflowGateway) -> None:
    req = _req(assignment_strategy="round_robin", assigned_role="reviewer")
    created = await gateway.create_request(req)
    assert created.assignment_strategy == "round_robin"


@pytest.mark.asyncio
async def test_assign_least_busy(gateway: HITLWorkflowGateway) -> None:
    req = _req(assignment_strategy="least_busy")
    created = await gateway.create_request(req)
    assert created.assignment_strategy == "least_busy"


@pytest.mark.asyncio
async def test_assign_skill_based(gateway: HITLWorkflowGateway) -> None:
    req = _req(assignment_strategy="skill_based", assigned_role="compliance")
    created = await gateway.create_request(req)
    assert created.assignment_strategy == "skill_based"


# ── Decision + audit trail ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decide_approve_sets_audit_trail(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req())
    decided = await gateway.decide(req.request_id, "approved", actor_id="user-abc", note="LGTM")
    assert decided.status == "approved"
    assert decided.reviewed_by == "user-abc"
    assert decided.reviewed_at is not None
    assert decided.note == "LGTM"
    assert decided.action_taken == "approved"


@pytest.mark.asyncio
async def test_decide_reject(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req())
    decided = await gateway.decide(req.request_id, "rejected", actor_id="user-xyz")
    assert decided.status == "rejected"
    assert decided.reviewed_by == "user-xyz"


@pytest.mark.asyncio
async def test_decide_idempotent(gateway: HITLWorkflowGateway) -> None:
    """Deciding twice on same request is a no-op (idempotent)."""
    req = await gateway.create_request(_req())
    d1 = await gateway.decide(req.request_id, "approved", actor_id="u1")
    d2 = await gateway.decide(req.request_id, "rejected", actor_id="u2", idempotent=True)
    assert d2.status == "approved"  # unchanged
    assert d2.reviewed_by == "u1"


@pytest.mark.asyncio
async def test_decide_with_form_data(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req())
    form = {"reason": "Verified manually", "confidence": 0.95}
    decided = await gateway.decide(req.request_id, "approved", actor_id="u1", form_data=form)
    assert decided.form_data == form


# ── Priority ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_priority_critical(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(priority="critical"))
    assert req.priority == "critical"


@pytest.mark.asyncio
async def test_priority_sorting(gateway: HITLWorkflowGateway) -> None:
    """Critical requests should appear before low priority in list."""
    await gateway.create_request(_req(priority="low"))
    await gateway.create_request(_req(priority="critical"))
    items, _ = await gateway.list_pending("tenant-1")
    assert items[0].priority == "critical"


# ── Delegation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(assigned_to="user-a"))
    delegated = await gateway.delegate(req.request_id, from_user="user-a", to_user="user-b", note="OOO")
    assert delegated.assigned_to == "user-b"
    assert any(c["type"] == "delegation" for c in delegated.discussion)


@pytest.mark.asyncio
async def test_delegate_non_pending_raises(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req())
    await gateway.decide(req.request_id, "approved", actor_id="u1")
    with pytest.raises(ValueError, match="pending"):
        await gateway.delegate(req.request_id, from_user="u1", to_user="u2")


# ── Escalation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_escalate(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(escalation_to_role="senior_reviewer"))
    escalated = await gateway.escalate(req.request_id, actor_id="u1", note="Timeout escalation")
    assert any(c["type"] == "escalation" for c in escalated.discussion)
    assert escalated.assigned_role == "senior_reviewer"


# ── Bulk decide ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_decide(gateway: HITLWorkflowGateway) -> None:
    r1 = await gateway.create_request(_req())
    r2 = await gateway.create_request(_req())
    results = await gateway.bulk_decide([r1.request_id, r2.request_id], "approved", "bulk-user")
    assert len(results) == 2
    assert all(r.status == "approved" for r in results)


# ── Magic link ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_magic_link(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req())
    link = await gateway.generate_magic_link(req.request_id, "approve")
    assert "magic" in link
    assert "approve" in link


@pytest.mark.asyncio
async def test_consume_magic_link_in_memory(gateway: HITLWorkflowGateway) -> None:
    """Without Redis, consume returns truthy payload."""
    req = await gateway.create_request(_req())
    link = await gateway.generate_magic_link(req.request_id, "approve")
    token = req.magic_link_token
    assert token is not None
    payload = await gateway.consume_magic_link(token)
    assert payload is not None


# ── Discussion thread ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_comment(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req())
    updated = await gateway.add_comment(req.request_id, actor_id="u1", comment="Looks good")
    assert any(c["type"] == "comment" and "Looks good" in c.get("text", "") for c in updated.discussion)


# ── Rich context helpers ──────────────────────────────────────────────────────


def test_make_context_item_json() -> None:
    item = make_context_item("json", "Payload", {"key": "val"})
    assert item["display_type"] == "json"
    assert item["data"] == {"key": "val"}


def test_make_context_item_all_types() -> None:
    for dtype in ("image", "json", "table", "diff", "chart", "number", "list"):
        item = make_context_item(dtype, "Test", "data")  # type: ignore[arg-type]
        assert item["display_type"] == dtype


def test_make_number_context_with_thresholds() -> None:
    item = make_number_context(
        "Risk Score", 0.85, unit="%", threshold_yellow=0.7, threshold_red=0.9
    )
    assert item["display_type"] == "number"
    assert item["threshold_yellow"] == 0.7
    assert item["threshold_red"] == 0.9
    assert item["data"]["value"] == 0.85


# ── Stats ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats(gateway: HITLWorkflowGateway) -> None:
    await gateway.create_request(_req())
    stats = await gateway.get_stats("tenant-1")
    assert "pending_count" in stats
    assert stats["pending_count"] >= 1


# ── Timeout action variants ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_action_auto_approve(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(timeout_action="auto_approve"))
    assert req.timeout_action == "auto_approve"


@pytest.mark.asyncio
async def test_timeout_action_auto_reject(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(timeout_action="auto_reject"))
    assert req.timeout_action == "auto_reject"


@pytest.mark.asyncio
async def test_timeout_action_escalate(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(timeout_action="escalate"))
    assert req.timeout_action == "escalate"


@pytest.mark.asyncio
async def test_timeout_action_pause(gateway: HITLWorkflowGateway) -> None:
    req = await gateway.create_request(_req(timeout_action="pause"))
    assert req.timeout_action == "pause"
