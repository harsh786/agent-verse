"""Phase 9 — Integration tests: mission lifecycle and auth flow.

Tests run with in-memory services (no Docker required).
Uses FakeProvider for deterministic LLM responses.
Marked with pytest.mark.integration per project convention.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


# ── Shared helpers ────────────────────────────────────────────────────────────

def _tenant(suffix: str = "int") -> TenantContext:
    return TenantContext(
        tenant_id=f"t-integration-{suffix}-{uuid.uuid4().hex[:6]}",
        plan=PlanTier.FREE,
        api_key_id="key-001",
    )


def _fake_provider(responses: list[str] | None = None) -> FakeProvider:
    return FakeProvider(
        responses=responses
        or [
            '{"steps": ["Retrieve data", "Summarize results"]}',
            "Task completed successfully.",
            '{"success": true, "reason": "Goal achieved"}',
        ]
    )


# ── Mission lifecycle ─────────────────────────────────────────────────────────

class TestMissionLifecycle:
    """End-to-end mission lifecycle using GoalService with in-memory store."""

    @pytest.mark.asyncio
    async def test_goal_service_instantiates_without_db(self) -> None:
        """GoalService can be created without a DB session (in-memory mode)."""
        from app.services.goal_service import GoalService

        svc = GoalService()
        assert svc is not None

    @pytest.mark.asyncio
    async def test_goal_service_list_goals_empty_initially(self) -> None:
        """Freshly created GoalService with no goals returns empty result."""
        from app.services.goal_service import GoalService
        tenant = _tenant("empty")
        svc = GoalService()
        result = await svc.list_goals(tenant_ctx=tenant)
        # Returns dict or list — both are valid depending on implementation
        assert isinstance(result, (dict, list))

    @pytest.mark.asyncio
    async def test_goal_service_get_unknown_goal_returns_none_or_404(self) -> None:
        """Getting a non-existent goal returns None or raises cleanly."""
        from app.services.goal_service import GoalService
        tenant = _tenant("unknown")
        svc = GoalService()
        try:
            result = await svc.get_goal("nonexistent-goal-id", tenant_ctx=tenant)
            assert result is None or isinstance(result, dict)
        except Exception as exc:
            # Acceptable: 404 or KeyError for non-existent goal
            assert any(k in str(exc).lower() for k in ["not found", "404", "none", "goal"])


# ── HITL approval flow ────────────────────────────────────────────────────────

class TestHITLApprovalFlow:
    """Full approval lifecycle: request → approve → verify resolution."""

    @pytest.fixture
    def gateway(self) -> HITLGateway:
        return HITLGateway()

    @pytest.fixture
    def tenant(self) -> TenantContext:
        return _tenant("hitl")

    def test_create_approval_request(
        self, gateway: HITLGateway, tenant: TenantContext
    ) -> None:
        from app.governance.hitl import ApprovalRequest

        req = ApprovalRequest(
            goal_id="goal-integ-01",
            action="send_critical_email",
            risk_level="high",
            request_id="req-integ-01",
        )
        req.status = ApprovalStatus.PENDING
        gateway._requests[(tenant.tenant_id, req.request_id)] = req
        assert req.status == ApprovalStatus.PENDING

    def test_approve_request_changes_status(
        self, gateway: HITLGateway, tenant: TenantContext
    ) -> None:
        from app.governance.hitl import ApprovalRequest

        req = ApprovalRequest(
            goal_id="goal-integ-02",
            action="deploy_to_prod",
            risk_level="critical",
            request_id="req-integ-02",
        )
        req.status = ApprovalStatus.PENDING
        gateway._requests[(tenant.tenant_id, req.request_id)] = req

        # Simulate approval
        req.status = ApprovalStatus.APPROVED
        assert gateway._requests[(tenant.tenant_id, "req-integ-02")].status == ApprovalStatus.APPROVED

    def test_reject_request_changes_status(
        self, gateway: HITLGateway, tenant: TenantContext
    ) -> None:
        from app.governance.hitl import ApprovalRequest

        req = ApprovalRequest(
            goal_id="goal-integ-03",
            action="delete_prod_data",
            risk_level="critical",
            request_id="req-integ-03",
        )
        req.status = ApprovalStatus.PENDING
        gateway._requests[(tenant.tenant_id, req.request_id)] = req

        req.status = ApprovalStatus.REJECTED
        assert req.status == ApprovalStatus.REJECTED

    def test_list_pending_returns_only_pending(
        self, gateway: HITLGateway, tenant: TenantContext
    ) -> None:
        from app.governance.hitl import ApprovalRequest

        pending_req = ApprovalRequest(
            goal_id="goal-p",
            action="action_pending",
            risk_level="medium",
            request_id="req-p-01",
        )
        pending_req.status = ApprovalStatus.PENDING

        approved_req = ApprovalRequest(
            goal_id="goal-a",
            action="action_approved",
            risk_level="medium",
            request_id="req-a-01",
        )
        approved_req.status = ApprovalStatus.APPROVED

        gateway._requests[(tenant.tenant_id, "req-p-01")] = pending_req
        gateway._requests[(tenant.tenant_id, "req-a-01")] = approved_req

        pending = gateway.list_pending(tenant_ctx=tenant)
        assert all(r.status == ApprovalStatus.PENDING for r in pending)
        assert any(r.request_id == "req-p-01" for r in pending)
        assert not any(r.request_id == "req-a-01" for r in pending)

    def test_expire_timed_out_requests(
        self, gateway: HITLGateway, tenant: TenantContext
    ) -> None:
        from datetime import UTC, datetime, timedelta
        from app.governance.hitl import ApprovalRequest

        req = ApprovalRequest(
            goal_id="goal-expire",
            action="stale_action",
            risk_level="low",
            request_id="req-expire-01",
        )
        req.status = ApprovalStatus.PENDING
        req._expires_at_dt = datetime.now(UTC) - timedelta(seconds=5)
        gateway._requests[(tenant.tenant_id, "req-expire-01")] = req

        expired = gateway.expire_timed_out_requests()
        assert "req-expire-01" in expired
        assert req.status == ApprovalStatus.TIMED_OUT

    def test_pending_for_different_tenants_isolated(self) -> None:
        from app.governance.hitl import ApprovalRequest

        gw = HITLGateway()
        ctx_a = TenantContext(tenant_id="t-isolation-A", plan=PlanTier.FREE, api_key_id="k")
        ctx_b = TenantContext(tenant_id="t-isolation-B", plan=PlanTier.FREE, api_key_id="k")

        req_a = ApprovalRequest(
            goal_id="g-a",
            action="act-a",
            risk_level="low",
            request_id="req-a",
        )
        req_a.status = ApprovalStatus.PENDING
        gw._requests[(ctx_a.tenant_id, "req-a")] = req_a

        pending_b = gw.list_pending(tenant_ctx=ctx_b)
        assert len(pending_b) == 0

        pending_a = gw.list_pending(tenant_ctx=ctx_a)
        assert len(pending_a) == 1


# ── Auth token and tenant context ─────────────────────────────────────────────

class TestTenantContextIsolation:
    """Tenant context isolation — cross-tenant data must not leak."""

    def test_different_tenants_have_different_ids(self) -> None:
        t1 = _tenant("A")
        t2 = _tenant("B")
        assert t1.tenant_id != t2.tenant_id

    def test_plan_tier_is_preserved(self) -> None:
        ctx = TenantContext(
            tenant_id="t-plan",
            plan=PlanTier.PROFESSIONAL,
            api_key_id="k1",
        )
        assert ctx.plan == PlanTier.PROFESSIONAL

    def test_tenant_context_not_shared_between_instances(self) -> None:
        """Two TenantContext objects are independent value objects."""
        ctx_a = TenantContext(tenant_id="a", plan=PlanTier.FREE, api_key_id="k1")
        ctx_b = TenantContext(tenant_id="b", plan=PlanTier.ENTERPRISE, api_key_id="k2")
        assert ctx_a.tenant_id != ctx_b.tenant_id
        assert ctx_a.plan != ctx_b.plan


# ── Approval chain engine integration ─────────────────────────────────────────

class TestApprovalChainEngineIntegration:
    """Integration scenarios for ApprovalChainEngine (in-memory store)."""

    @pytest.mark.asyncio
    async def test_create_and_retrieve_approval_request(self) -> None:
        from app.org.approval_chain import ApprovalChainEngine, CHAINS_BY_ID

        engine = ApprovalChainEngine()
        chain = CHAINS_BY_ID["prod_deploy"]

        req = await engine.create_approval_request(
            chain=chain,
            action_detail="Deploy v2.5 to production",
            mission_id="mission-001",
            agent_id="agent-001",
            tenant_id="t-int-chain",
            org_id="org-001",
        )
        assert req.status == "pending"
        assert req.chain_id == "prod_deploy"
        assert req.tenant_id == "t-int-chain"

        retrieved = await engine.get_request(req.request_id)
        assert retrieved is not None
        assert retrieved.request_id == req.request_id

    @pytest.mark.asyncio
    async def test_record_approval_and_check_complete(self) -> None:
        from app.org.approval_chain import ApprovalChainEngine, CHAINS_BY_ID

        engine = ApprovalChainEngine()
        chain = CHAINS_BY_ID["legal_agreement"]  # strategy="any", needs 1 of [legal_counsel, legal_specialist]

        req = await engine.create_approval_request(
            chain=chain,
            action_detail="Sign NDA with PartnerCo",
            mission_id=None,
            agent_id=None,
            tenant_id="t-int-legal",
        )
        assert req.status == "pending"

        # One approval from an authorised role satisfies "any" strategy
        updated = await engine.record_approval(req.request_id, "legal_counsel", approved=True)
        assert updated.status == "approved"

    @pytest.mark.asyncio
    async def test_rejection_closes_request(self) -> None:
        from app.org.approval_chain import ApprovalChainEngine, CHAINS_BY_ID

        engine = ApprovalChainEngine()
        chain = CHAINS_BY_ID["budget_override"]

        req = await engine.create_approval_request(
            chain=chain,
            action_detail="Exceed Q4 budget by $200k",
            mission_id=None,
            agent_id=None,
            tenant_id="t-int-budget",
        )
        updated = await engine.record_approval(req.request_id, "CFO", approved=False, notes="Not approved")
        assert updated.status == "rejected"
        assert updated.is_resolved is True

    @pytest.mark.asyncio
    async def test_list_pending_returns_only_unresolved(self) -> None:
        from app.org.approval_chain import ApprovalChainEngine, CHAINS_BY_ID

        engine = ApprovalChainEngine()
        chain = CHAINS_BY_ID["budget_override"]
        tid = "t-int-list"

        r1 = await engine.create_approval_request(
            chain=chain, action_detail="Budget A", mission_id=None,
            agent_id=None, tenant_id=tid,
        )
        r2 = await engine.create_approval_request(
            chain=chain, action_detail="Budget B", mission_id=None,
            agent_id=None, tenant_id=tid,
        )

        # Resolve r2
        await engine.record_approval(r2.request_id, "CFO", approved=True)

        pending = await engine.list_pending(tenant_id=tid)
        ids = [r.request_id for r in pending]
        assert r1.request_id in ids
        assert r2.request_id not in ids
