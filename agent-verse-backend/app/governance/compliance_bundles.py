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
from typing import Any

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
        invalidate_active_bundles(tenant_id)
        logger.info("compliance_bundle_enabled", tenant=tenant_id, bundle=bundle_id)

    def disable(self, tenant_id: str, bundle_id: str) -> None:
        self._tenant_bundles.get(tenant_id, set()).discard(bundle_id)

    def active_bundle_ids(self, tenant_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                bid
                for bid in self._tenant_bundles.get(tenant_id, set())
                if bid in COMPLIANCE_BUNDLES
            )
        )

    def get_active(self, tenant_id: str) -> list[ComplianceBundle]:
        return [COMPLIANCE_BUNDLES[bid] for bid in self.active_bundle_ids(tenant_id)]

    def get_effective_max_autonomy(self, tenant_id: str) -> str:
        """Return the most restrictive autonomy mode across all active bundles."""
        bundles = self.get_active(tenant_id)
        if not bundles:
            return "fully-autonomous"
        return _most_restrictive([b.max_autonomy_mode for b in bundles])

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


def _most_restrictive(modes: list[str]) -> str:
    """Return the most restrictive autonomy mode in ``modes``."""
    if "supervised" in modes:
        return "supervised"
    if "bounded-autonomous" in modes:
        return "bounded-autonomous"
    return "fully-autonomous"


class PostgresComplianceBundleStore:
    """Durable per-tenant compliance-bundle enablement.

    The in-memory :class:`ComplianceBundleManager` is fine for a single-process
    development run, but a compliance posture that lives in one replica's heap is
    not a posture: it is invisible to every other replica and to every Celery
    worker — which is where agents actually execute and therefore where an
    autonomy ceiling has to bind — and it disappears on restart.

    Same method names as the in-memory manager (plus async), so the lifespan can
    swap one for the other the way it does for every other service.
    """

    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def enable(self, tenant_id: str, bundle_id: str, *, actor: str = "") -> None:
        if bundle_id not in COMPLIANCE_BUNDLES:
            raise ValueError(f"Unknown compliance bundle: {bundle_id}")
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._sf() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO tenant_compliance_bundles "
                    "(tenant_id, bundle_id, enabled_by) "
                    "VALUES (:tid, :bid, :actor) "
                    "ON CONFLICT (tenant_id, bundle_id) DO NOTHING"
                ),
                {"tid": tenant_id, "bid": bundle_id, "actor": actor},
            )
        invalidate_active_bundles(tenant_id)
        logger.info("compliance_bundle_enabled", tenant=tenant_id, bundle=bundle_id)

    async def disable(self, tenant_id: str, bundle_id: str) -> None:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._sf() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    "DELETE FROM tenant_compliance_bundles "
                    "WHERE tenant_id = :tid AND bundle_id = :bid"
                ),
                {"tid": tenant_id, "bid": bundle_id},
            )
        invalidate_active_bundles(tenant_id)
        logger.info("compliance_bundle_disabled", tenant=tenant_id, bundle=bundle_id)

    async def active_bundle_ids(self, tenant_id: str) -> tuple[str, ...]:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._sf() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT bundle_id FROM tenant_compliance_bundles "
                        "WHERE tenant_id = :tid ORDER BY bundle_id"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
        return tuple(str(r[0]) for r in rows if str(r[0]) in COMPLIANCE_BUNDLES)

    async def get_active(self, tenant_id: str) -> list[ComplianceBundle]:
        return [COMPLIANCE_BUNDLES[bid] for bid in await self.active_bundle_ids(tenant_id)]

    async def effective_max_autonomy(self, tenant_id: str) -> str:
        bundles = await self.get_active(tenant_id)
        if not bundles:
            return "fully-autonomous"
        return _most_restrictive([b.max_autonomy_mode for b in bundles])

    async def requires_hitl_for_tool(self, tenant_id: str, tool_name: str) -> bool:
        for bundle in await self.get_active(tenant_id):
            for pattern in bundle.required_hitl_for:
                if (
                    pattern.endswith("*") and tool_name.startswith(pattern[:-1])
                ) or tool_name == pattern:
                    return True
        return False


# ── Store-agnostic helpers ────────────────────────────────────────────────────
#
# ``app.state.compliance_bundle_store`` is the in-memory manager before the
# lifespan runs and ``PostgresComplianceBundleStore`` after it, exactly like
# every other two-phase service. The Postgres methods are async and the
# in-memory ones are not, so callers go through these instead of caring which
# one they hold.


async def _maybe_await(value: Any) -> Any:
    import inspect

    return await value if inspect.isawaitable(value) else value


async def enable_bundle(store: Any, tenant_id: str, bundle_id: str, *, actor: str = "") -> None:
    """Enable ``bundle_id`` for ``tenant_id`` on whichever store is wired."""
    if store is None:
        raise ValueError("compliance bundle store unavailable")
    try:
        await _maybe_await(store.enable(tenant_id, bundle_id, actor=actor))
    except TypeError:
        # In-memory manager takes no ``actor``.
        await _maybe_await(store.enable(tenant_id, bundle_id))


async def disable_bundle(store: Any, tenant_id: str, bundle_id: str) -> None:
    if store is None:
        raise ValueError("compliance bundle store unavailable")
    await _maybe_await(store.disable(tenant_id, bundle_id))


async def active_bundle_ids_for(store: Any, tenant_id: str) -> tuple[str, ...]:
    if store is None:
        return ()
    return tuple(await _maybe_await(store.active_bundle_ids(tenant_id)))


async def effective_max_autonomy_for(store: Any, tenant_id: str) -> str:
    """Most restrictive autonomy mode across the tenant's enabled bundles."""
    if store is None:
        return "fully-autonomous"
    getter = getattr(store, "effective_max_autonomy", None) or store.get_effective_max_autonomy
    return str(await _maybe_await(getter(tenant_id)))


# tenant_id → (monotonic loaded_at, active bundle ids): the per-tool-call bundle
# check must not be a DB round trip; a change binds fleet-wide within the TTL.
_ACTIVE_BUNDLES_TTL_S = 15.0
_ACTIVE_BUNDLES_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}


def invalidate_active_bundles(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _ACTIVE_BUNDLES_CACHE.clear()
    else:
        _ACTIVE_BUNDLES_CACHE.pop(tenant_id, None)


def _tool_matches(patterns: list[str], tool_name: str) -> bool:
    return any(
        (p.endswith("*") and tool_name.startswith(p[:-1])) or tool_name == p for p in patterns
    )


async def bundle_hitl_requirement(db_factory: Any, tenant_id: str, tool_name: str) -> str | None:
    """Which enabled compliance bundle requires a human approval for *tool_name*.

    Returns the bundle id, or ``None``. ``required_hitl_for`` used to have no
    caller at all (TRUST-02). Raises when the tenant's bundles cannot be read and
    nothing is cached — the caller must then require approval (fail closed).
    """
    import time

    if db_factory is None or not tenant_id or not tool_name:
        return None
    now = time.monotonic()
    hit = _ACTIVE_BUNDLES_CACHE.get(tenant_id)
    if hit is not None and now - hit[0] < _ACTIVE_BUNDLES_TTL_S:
        bundle_ids = hit[1]
    else:
        try:
            bundle_ids = await PostgresComplianceBundleStore(db_factory).active_bundle_ids(
                tenant_id
            )
        except Exception:
            if hit is None:
                raise
            bundle_ids = hit[1]  # stale but real
        else:
            _ACTIVE_BUNDLES_CACHE[tenant_id] = (now, bundle_ids)
    for bid in bundle_ids:
        if _tool_matches(COMPLIANCE_BUNDLES[bid].required_hitl_for, tool_name):
            return bid
    return None


async def requires_hitl_for_tool_on(store: Any, tenant_id: str, tool_name: str) -> bool:
    if store is None:
        return False
    return bool(await _maybe_await(store.requires_hitl_for_tool(tenant_id, tool_name)))
