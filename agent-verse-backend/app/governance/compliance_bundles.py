"""
Compliance Bundles
===================
Pre-configured governance, guardrail, and policy bundles for regulated verticals.

When a tenant enables a compliance bundle, ALL of these settings are automatically applied:
- HIPAA (healthcare): PHI guardrails, encrypted audit, BAA acknowledgement
- SOC2: audit export, access reviews, 90-day log retention
- GDPR: data erasure right, consent tracking, DPA
- PCI-DSS: card data masking, no storage in logs, quarterly access review
- India DPDP: data residency, consent management, grievance officer

Bundles are additive — a tenant can enable multiple.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ComplianceBundle:
    id: str
    name: str
    description: str
    required_guardrail_layers: list[str]
    required_hitl_for: list[str]  # tool patterns requiring HITL
    max_autonomy_mode: str  # supervised | bounded-autonomous | fully-autonomous
    audit_retention_days: int
    pii_fields_masked: list[str]
    required_policies: list[str]
    data_residency_required: bool = False


COMPLIANCE_BUNDLES: dict[str, ComplianceBundle] = {
    "hipaa": ComplianceBundle(
        id="hipaa",
        name="HIPAA (Healthcare)",
        description="HIPAA-compliant configuration for healthcare agents handling PHI",
        required_guardrail_layers=["pii_scanner", "phi_detector", "output_scanner"],
        required_hitl_for=[
            "send_email",
            "send_slack_message",
            "create_*_record",
            "update_patient_*",
        ],
        max_autonomy_mode="supervised",
        audit_retention_days=2190,  # 6 years per HIPAA
        pii_fields_masked=["ssn", "dob", "mrn", "patient_id", "phone", "address"],
        required_policies=["no_phi_in_logs", "encrypt_at_rest", "mfa_required"],
        data_residency_required=True,
    ),
    "gdpr": ComplianceBundle(
        id="gdpr",
        name="GDPR (EU Data Protection)",
        description="GDPR-compliant configuration for EU personal data processing",
        required_guardrail_layers=["pii_scanner", "output_scanner", "consent_check"],
        required_hitl_for=["delete_user_*", "export_user_data", "transfer_to_third_party"],
        max_autonomy_mode="bounded-autonomous",
        audit_retention_days=2555,  # 7 years
        pii_fields_masked=["email", "name", "phone", "ip_address", "location"],
        required_policies=["right_to_erasure", "consent_required", "dpa_required"],
        data_residency_required=True,
    ),
    "soc2": ComplianceBundle(
        id="soc2",
        name="SOC 2 Type II",
        description="SOC 2 compliance configuration — availability, security, confidentiality",
        required_guardrail_layers=["injection_scanner", "output_scanner"],
        required_hitl_for=["grant_access_*", "create_admin_*", "delete_*"],
        max_autonomy_mode="bounded-autonomous",
        audit_retention_days=365,  # 1 year minimum
        pii_fields_masked=["api_key", "password", "secret", "token"],
        required_policies=["quarterly_access_review", "incident_response", "change_management"],
    ),
    "india_dpdp": ComplianceBundle(
        id="india_dpdp",
        name="India DPDP Act 2023",
        description="India Digital Personal Data Protection Act compliance",
        required_guardrail_layers=["pii_scanner", "consent_check"],
        required_hitl_for=["share_personal_data", "transfer_abroad", "delete_principal_data"],
        max_autonomy_mode="bounded-autonomous",
        audit_retention_days=1825,  # 5 years
        pii_fields_masked=["aadhaar", "pan", "phone", "email", "name"],
        required_policies=[
            "consent_purpose_tracking",
            "grievance_officer",
            "data_principal_rights",
        ],
        data_residency_required=True,
    ),
    "pci_dss": ComplianceBundle(
        id="pci_dss",
        name="PCI-DSS (Payment Cards)",
        description="PCI-DSS compliance for agents handling payment card data",
        required_guardrail_layers=["pii_scanner", "output_scanner", "card_data_detector"],
        required_hitl_for=["process_payment", "store_card_*", "refund_*"],
        max_autonomy_mode="supervised",
        audit_retention_days=365,
        pii_fields_masked=["card_number", "cvv", "expiry", "cardholder_name"],
        required_policies=["no_card_storage", "tokenization_required", "quarterly_scan"],
        data_residency_required=False,
    ),
}


class ComplianceBundleManager:
    """Manages active compliance bundles per tenant."""

    def __init__(self) -> None:
        self._tenant_bundles: dict[str, set[str]] = {}

    def enable(self, tenant_id: str, bundle_id: str) -> None:
        if bundle_id not in COMPLIANCE_BUNDLES:
            raise ValueError(f"Unknown compliance bundle: {bundle_id}")
        self._tenant_bundles.setdefault(tenant_id, set()).add(bundle_id)
        logger.info("compliance_bundle_enabled", tenant=tenant_id, bundle=bundle_id)

    def disable(self, tenant_id: str, bundle_id: str) -> None:
        self._tenant_bundles.get(tenant_id, set()).discard(bundle_id)

    def get_active(self, tenant_id: str) -> list[ComplianceBundle]:
        bundle_ids = self._tenant_bundles.get(tenant_id, set())
        return [COMPLIANCE_BUNDLES[bid] for bid in bundle_ids if bid in COMPLIANCE_BUNDLES]

    def get_effective_max_autonomy(self, tenant_id: str) -> str:
        """Return the most restrictive autonomy mode across all active bundles."""
        bundles = self.get_active(tenant_id)
        if not bundles:
            return "fully-autonomous"
        modes = [b.max_autonomy_mode for b in bundles]
        if "supervised" in modes:
            return "supervised"
        if "bounded-autonomous" in modes:
            return "bounded-autonomous"
        return "fully-autonomous"

    def requires_hitl_for_tool(self, tenant_id: str, tool_name: str) -> bool:
        """Check if any active bundle requires HITL for this tool."""
        for bundle in self.get_active(tenant_id):
            for pattern in bundle.required_hitl_for:
                if (
                    pattern.endswith("*") and tool_name.startswith(pattern[:-1])
                ) or tool_name == pattern:
                    return True
        return False


# Module singleton
_bundle_manager = ComplianceBundleManager()
