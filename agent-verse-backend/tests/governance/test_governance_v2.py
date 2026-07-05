"""Tests for Governance v2 — time-based rules, four-eyes, compliance bundles."""
import datetime

import pytest

from app.governance.compliance_bundles import COMPLIANCE_BUNDLES, ComplianceBundleManager
from app.governance.time_policy import TimePolicyEngine, TimeRule


class TestTimePolicyEngine:
    def _make(self) -> TimePolicyEngine:
        return TimePolicyEngine()

    def test_allows_safe_tool_anytime(self) -> None:
        engine = self._make()
        allowed, reason = engine.check_tool("jira_search_issues")
        assert allowed is True

    def test_blocks_delete_during_night(self) -> None:
        engine = self._make()
        night_time = datetime.datetime(2026, 7, 5, 23, 0, 0, tzinfo=datetime.timezone.utc)
        allowed, reason = engine.check_tool("delete_all_records", now=night_time)
        assert allowed is False
        assert len(reason) > 0

    def test_allows_delete_during_day(self) -> None:
        engine = self._make()
        day_time = datetime.datetime(2026, 7, 7, 14, 0, 0, tzinfo=datetime.timezone.utc)  # Monday 2pm
        allowed, reason = engine.check_tool("delete_old_logs", now=day_time)
        assert allowed is True

    def test_blocks_prod_deploy_on_weekend(self) -> None:
        engine = self._make()
        saturday = datetime.datetime(2026, 7, 4, 14, 0, 0, tzinfo=datetime.timezone.utc)
        allowed, reason = engine.check_tool("deploy_to_prod", now=saturday)
        assert allowed is False

    def test_allows_prod_deploy_on_weekday(self) -> None:
        engine = self._make()
        monday = datetime.datetime(2026, 7, 6, 14, 0, 0, tzinfo=datetime.timezone.utc)
        allowed, reason = engine.check_tool("deploy_to_prod", now=monday)
        assert allowed is True

    def test_blackout_window_blocks_all(self) -> None:
        engine = self._make()
        now = datetime.datetime(2026, 7, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
        start = now - datetime.timedelta(hours=1)
        end = now + datetime.timedelta(hours=1)
        engine.add_blackout(start, end, "Maintenance window")
        allowed, reason = engine.check_tool("jira_search_issues", now=now)
        assert allowed is False
        assert "Maintenance" in reason

    def test_custom_rule(self) -> None:
        rule = TimeRule(
            name="no_billing_changes",
            tool_patterns=["update_billing_*"],
            blocked_weekdays=[5, 6],  # weekends
            reason="Billing changes blocked on weekends",
        )
        engine = TimePolicyEngine(custom_rules=[rule])
        saturday = datetime.datetime(2026, 7, 4, 10, 0, 0, tzinfo=datetime.timezone.utc)
        allowed, _ = engine.check_tool("update_billing_plan", now=saturday)
        assert allowed is False


class TestComplianceBundles:
    def test_all_bundles_valid(self) -> None:
        assert len(COMPLIANCE_BUNDLES) >= 4
        for bid, bundle in COMPLIANCE_BUNDLES.items():
            assert bundle.id == bid
            assert bundle.name
            assert bundle.audit_retention_days > 0

    def test_enable_and_list(self) -> None:
        mgr = ComplianceBundleManager()
        mgr.enable("t1", "hipaa")
        active = mgr.get_active("t1")
        assert any(b.id == "hipaa" for b in active)

    def test_enable_unknown_raises(self) -> None:
        mgr = ComplianceBundleManager()
        with pytest.raises(ValueError):
            mgr.enable("t1", "nonexistent-bundle")

    def test_effective_max_autonomy_most_restrictive(self) -> None:
        mgr = ComplianceBundleManager()
        mgr.enable("t1", "hipaa")  # supervised
        mgr.enable("t1", "soc2")   # bounded-autonomous
        assert mgr.get_effective_max_autonomy("t1") == "supervised"

    def test_requires_hitl_for_tool(self) -> None:
        mgr = ComplianceBundleManager()
        mgr.enable("t1", "hipaa")
        assert mgr.requires_hitl_for_tool("t1", "send_email") is True
        assert mgr.requires_hitl_for_tool("t1", "jira_search_issues") is False

    def test_disable_bundle(self) -> None:
        mgr = ComplianceBundleManager()
        mgr.enable("t1", "gdpr")
        mgr.disable("t1", "gdpr")
        assert mgr.get_effective_max_autonomy("t1") == "fully-autonomous"

    def test_hipaa_has_phi_fields_masked(self) -> None:
        hipaa = COMPLIANCE_BUNDLES["hipaa"]
        assert "ssn" in hipaa.pii_fields_masked
        assert "mrn" in hipaa.pii_fields_masked

    def test_india_dpdp_requires_aadhaar_masking(self) -> None:
        dpdp = COMPLIANCE_BUNDLES["india_dpdp"]
        assert "aadhaar" in dpdp.pii_fields_masked
        assert "pan" in dpdp.pii_fields_masked
        assert dpdp.data_residency_required is True
