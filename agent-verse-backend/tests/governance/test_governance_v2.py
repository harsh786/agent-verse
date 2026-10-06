"""Tests for Governance v2 — four-eyes, compliance bundles.

Time-based rules are tenant policy time windows (app.governance.policies);
the unused app.governance.time_policy module was removed (a03-F060-02).
"""

import pytest

from app.governance.compliance_bundles import COMPLIANCE_BUNDLES, ComplianceBundleManager


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
