"""Tests for Audit v3, Scopes v2, and Limits v2."""
import json

import pytest

from app.auth.custom_roles import CustomRole, CustomRoleStore, _BASE_ROLE_SCOPES
from app.auth.temp_elevation import grant_elevation, verify_elevation
from app.governance.audit_v3 import AuditV3, _hash_dict, compute_entry_hash
from app.tenancy.limits_v2 import PLAN_LIMITS_V2, LimitsV2Checker


class TestAuditV3:
    @pytest.mark.asyncio
    async def test_append_creates_record(self):
        audit = AuditV3()
        record = await audit.append(
            tenant_id="t1", goal_id="g1", action="tool_call",
            tool_name="jira_search", actor="agent:a1",
        )
        assert record.id is not None
        assert record.previous_hash == "genesis"
        assert record.entry_hash != ""

    @pytest.mark.asyncio
    async def test_chain_links_correctly(self):
        audit = AuditV3()
        r1 = await audit.append(tenant_id="t1", goal_id="g1", action="step_1", tool_name="jira")
        r2 = await audit.append(tenant_id="t1", goal_id="g1", action="step_2", tool_name="slack")
        assert r2.previous_hash == r1.entry_hash

    @pytest.mark.asyncio
    async def test_chain_verification_passes(self):
        audit = AuditV3()
        for i in range(5):
            await audit.append(tenant_id="t1", goal_id="g1", action=f"step_{i}")
        result = audit.verify_chain("t1")
        assert result["valid"] is True
        assert result["records_checked"] == 5

    @pytest.mark.asyncio
    async def test_tampered_record_detected(self):
        audit = AuditV3()
        await audit.append(tenant_id="t1", goal_id="g1", action="step_1")
        await audit.append(tenant_id="t1", goal_id="g1", action="step_2")
        # Tamper r1's hash
        audit._records[0].entry_hash = "tampered_hash"
        result = audit.verify_chain("t1")
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_export_json(self):
        audit = AuditV3()
        await audit.append(tenant_id="t1", goal_id="g1", action="test")
        data = audit.export_records("t1", fmt="json")
        records = json.loads(data)
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_export_csv(self):
        audit = AuditV3()
        await audit.append(tenant_id="t1", goal_id="g1", action="test")
        csv_data = audit.export_records("t1", fmt="csv")
        assert "entry_hash" in csv_data
        assert "action" in csv_data

    @pytest.mark.asyncio
    async def test_tenant_isolation(self):
        audit = AuditV3()
        await audit.append(tenant_id="t1", goal_id="g1", action="a")
        await audit.append(tenant_id="t2", goal_id="g2", action="b")
        t1_result = audit.verify_chain("t1")
        t2_result = audit.verify_chain("t2")
        assert t1_result["valid"] is True
        assert t2_result["valid"] is True

    def test_hash_is_deterministic(self):
        h1 = compute_entry_hash(
            previous_hash="prev", timestamp="2026-07-05T00:00:00",
            tenant_id="t1", goal_id="g1", action="test",
            tool_name="jira", tool_args_hash="argshash",
            actor="agent:a1", actor_ip="1.2.3.4",
            delegation_chain_hash="delhash", metadata_hash="metahash",
        )
        h2 = compute_entry_hash(
            previous_hash="prev", timestamp="2026-07-05T00:00:00",
            tenant_id="t1", goal_id="g1", action="test",
            tool_name="jira", tool_args_hash="argshash",
            actor="agent:a1", actor_ip="1.2.3.4",
            delegation_chain_hash="delhash", metadata_hash="metahash",
        )
        assert h1 == h2


class TestCustomRoles:
    def test_base_roles_defined(self):
        assert "viewer" in _BASE_ROLE_SCOPES
        assert "admin" in _BASE_ROLE_SCOPES
        assert "owner" in _BASE_ROLE_SCOPES

    def test_owner_has_wildcard(self):
        assert "*" in _BASE_ROLE_SCOPES["owner"]

    def test_admin_has_audit_read(self):
        assert "audit:read" in _BASE_ROLE_SCOPES["admin"]

    def test_custom_role_inherits_from_base(self):
        store = CustomRoleStore()
        role = CustomRole(
            id="r1", tenant_id="t1", name="data-entry",
            inherits="viewer", extra_scopes=["goals:write"],
        )
        store.define(role)
        scopes = store.resolve_scopes("t1", "data-entry")
        assert "goals:write" in scopes
        assert "goals:read" in scopes  # inherited

    def test_custom_role_denied_scope_removed(self):
        role = CustomRole(
            id="r1", tenant_id="t1", name="read-only-goals",
            inherits="operator", denied_scopes=["agents:run", "goals:write"],
        )
        scopes = role.effective_scopes()
        assert "agents:run" not in scopes
        assert "goals:write" not in scopes
        assert "goals:read" in scopes  # still there

    def test_unknown_role_no_scopes_failclosed(self):
        store = CustomRoleStore()
        scopes = store.resolve_scopes("t1", "nonexistent-role")
        assert len(scopes) == 0

    def test_has_scope_builtin_role(self):
        store = CustomRoleStore()
        assert store.has_scope("t1", "admin", "audit:read") is True
        assert store.has_scope("t1", "viewer", "agents:write") is False


class TestTemporalElevation:
    def test_grant_and_verify(self):
        elev = grant_elevation(
            tenant_id="t1", user_id="alice", original_role="viewer",
            elevated_role="admin", granted_by="bob",
            reason="Support ticket #123", duration_seconds=3600,
        )
        payload = verify_elevation(elev.token)
        assert payload is not None
        assert payload["user_id"] == "alice"
        assert payload["elevated_role"] == "admin"

    def test_expired_elevation_invalid(self):
        elev = grant_elevation(
            tenant_id="t1", user_id="alice", original_role="viewer",
            elevated_role="admin", granted_by="bob",
            reason="test", duration_seconds=-1,
        )
        assert verify_elevation(elev.token) is None

    def test_max_duration_enforced(self):
        with pytest.raises(ValueError, match="4 hours"):
            grant_elevation(
                tenant_id="t1", user_id="alice", original_role="viewer",
                elevated_role="owner", granted_by="bob",
                reason="test", duration_seconds=86400,
            )

    def test_tampered_token_invalid(self):
        elev = grant_elevation(
            tenant_id="t1", user_id="alice", original_role="viewer",
            elevated_role="admin", granted_by="bob",
            reason="test",
        )
        tampered = elev.token[:-10] + "TAMPERED"
        assert verify_elevation(tampered) is None


class TestLimitsV2:
    def test_step_limit_free_plan(self):
        checker = LimitsV2Checker()
        ok, _ = checker.check_step_limit("free", 3)
        assert ok is True
        ok, reason = checker.check_step_limit("free", 6)
        assert ok is False
        assert "Step limit" in reason

    def test_step_limit_enterprise_more_steps(self):
        checker = LimitsV2Checker()
        ok, _ = checker.check_step_limit("enterprise", 40)
        assert ok is True

    def test_token_limit_free_plan(self):
        checker = LimitsV2Checker()
        ok, _ = checker.check_token_limit("free", 10000)
        assert ok is True
        ok, reason = checker.check_token_limit("free", 100000)
        assert ok is False

    def test_connector_rate_limit(self):
        checker = LimitsV2Checker()
        # Free plan: jira = 10 rpm
        for _ in range(10):
            ok, _ = checker.check_connector_rate("free", "jira", "t1")
            assert ok is True
        # 11th call should be blocked
        ok, reason = checker.check_connector_rate("free", "jira", "t1")
        assert ok is False
        assert "rate limit" in reason.lower()

    def test_burst_rate_limit(self):
        checker = LimitsV2Checker()
        # Free plan: 10 burst/10s
        for _ in range(10):
            ok, _ = checker.check_burst_rate("free", "t_burst")
            assert ok is True
        ok, reason = checker.check_burst_rate("free", "t_burst")
        assert ok is False
        assert "Burst" in reason

    def test_plan_monotone_limits(self):
        plans = ["free", "starter", "professional", "enterprise"]
        for i in range(len(plans) - 1):
            lower = PLAN_LIMITS_V2[plans[i]]
            higher = PLAN_LIMITS_V2[plans[i + 1]]
            assert higher.max_steps_per_goal >= lower.max_steps_per_goal
            assert higher.max_input_tokens_per_goal >= lower.max_input_tokens_per_goal
            assert higher.knowledge_storage_mb >= lower.knowledge_storage_mb
