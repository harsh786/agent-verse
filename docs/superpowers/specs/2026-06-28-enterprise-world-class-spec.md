# Enterprise Features — World-Class Specification

**Area 8 · Migration 0060 · Version 1.0 · 2026-06-28**

---

## 1. Vision

Enterprise sales are blocked by three categories of issues in the current AgentVerse platform: legal compliance claims that are demonstrably false (the code asserts `gdpr_compliant=True` regardless of actual tenant configuration, which is both wrong and a potential regulatory liability), identity federation that doesn't exist (no SAML 2.0, no SCIM, meaning enterprise IT cannot connect their Okta/Azure AD/Ping identity providers), and security controls that fall short of regulated industry requirements (HIPAA minimum necessary principle not enforced, Business Associate Agreement verification absent, UUID v4 API keys that are not NIST-compliant). A single enterprise contract — healthcare network, investment bank, law firm, or government agency — is worth 100 to 1000x the value of a typical SMB tenant, and these three blockers prevent all of them from signing.

This specification closes every identified enterprise gap. SAML 2.0 single sign-on via `python3-saml` enables true federation with enterprise identity providers; SCIM 2.0 provisioning enables automated user lifecycle management (create user in Okta → instant AgentVerse account, deactivate in Okta → instant revocation). HIPAA controls include minimum necessary access enforcement, PHI access logging with audit entries that satisfy the HIPAA Security Rule §164.312(b), and a Business Associate Agreement verification gate that prevents clinical tools from being used until the BAA is countersigned. The GDPR export endpoint is corrected to export ALL goals (not a 500-record cap), and the retention sweep is fixed to actually delete data. API keys are generated using `secrets.token_urlsafe(32)` (128-bit entropy, NIST SP 800-131A compliant). White-label configuration enables complete platform rebranding for OEM customers.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| GDPR compliance claim | `gdpr_compliant=True` hardcoded | Legally dangerous false assertion | CRITICAL |
| GDPR data export | Caps at 500 goals | GDPR Art. 20 violation (right to portability) | CRITICAL |
| Retention sweep | Not implemented | Data not deleted per retention policy | CRITICAL |
| API key entropy | UUID v4 (122 bits) | Not NIST SP 800-131A compliant | HIGH |
| SAML 2.0 | Not implemented | Cannot federate with enterprise IdPs | HIGH |
| SCIM 2.0 | Not implemented | No automated user provisioning | HIGH |
| HIPAA controls | Missing minimum necessary, BAA | Healthcare enterprise blocker | HIGH |
| Legal holds enforcement | Schema only, no enforcement | Legal holds can be bypassed | HIGH |
| White-label | Not implemented | OEM customers cannot rebrand | MEDIUM |
| SOC 2 controls | Partial | Audit trail incomplete (addressed in Spec 4) | MEDIUM |
| Multi-region data residency | Hardcoded | Cannot direct EU tenants to EU infra | MEDIUM |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0060

```sql
-- =============================================================================
-- Migration 0060: Enterprise contracts, compliance certifications, SAML configs
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: enterprise_contracts
-- Tracks signed agreements (BAA, DPA, MSA, etc.)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS enterprise_contracts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    contract_type   TEXT NOT NULL
                    CHECK (contract_type IN ('baa', 'dpa', 'msa', 'nda', 'sla', 'custom')),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'signed', 'expired', 'terminated')),
    version         TEXT NOT NULL DEFAULT '1.0',
    signed_by_name  TEXT,
    signed_by_email TEXT,
    signed_at       TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    document_url    TEXT,                           -- secure S3 URL
    document_hash   TEXT,                           -- SHA-256 of signed document
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_enterprise_contracts_tenant
    ON enterprise_contracts(tenant_id, contract_type)
    WHERE status = 'signed';

ALTER TABLE enterprise_contracts ENABLE ROW LEVEL SECURITY;
CREATE POLICY enterprise_contracts_isolation ON enterprise_contracts
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: saml_configs
-- Per-tenant SAML 2.0 IdP configuration
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS saml_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    idp_entity_id   TEXT NOT NULL,
    idp_sso_url     TEXT NOT NULL,
    idp_cert        TEXT NOT NULL,                  -- IdP X.509 certificate (PEM)
    sp_entity_id    TEXT NOT NULL,
    attribute_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- e.g. {"email": "urn:oid:0.9.2342...", "first_name": "...", "department": "..."}
    default_role    TEXT NOT NULL DEFAULT 'viewer',
    jit_provisioning BOOLEAN NOT NULL DEFAULT TRUE, -- create users on first login
    force_authn     BOOLEAN NOT NULL DEFAULT FALSE,
    name_id_format  TEXT NOT NULL DEFAULT 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE saml_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY saml_configs_isolation ON saml_configs
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: scim_configs
-- SCIM 2.0 provisioning configuration
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS scim_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    bearer_token_hash TEXT NOT NULL,               -- SHA-256 of SCIM bearer token
    bearer_token_prefix TEXT NOT NULL,             -- for display only
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    allow_user_create BOOLEAN NOT NULL DEFAULT TRUE,
    allow_user_update BOOLEAN NOT NULL DEFAULT TRUE,
    allow_user_delete BOOLEAN NOT NULL DEFAULT FALSE,
    allow_group_sync  BOOLEAN NOT NULL DEFAULT TRUE,
    default_role    TEXT NOT NULL DEFAULT 'viewer',
    group_role_map  JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- {"Engineering": "developer", "Compliance": "viewer", "Leadership": "admin"}
    last_sync_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE scim_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY scim_configs_isolation ON scim_configs
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: compliance_certifications
-- Tracks per-tenant compliance status (dynamically computed, not hardcoded)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS compliance_certifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    framework       TEXT NOT NULL
                    CHECK (framework IN ('hipaa', 'gdpr', 'sox', 'pci_dss', 'iso27001', 'soc2')),
    status          TEXT NOT NULL
                    CHECK (status IN ('not_configured', 'partial', 'compliant', 'non_compliant')),
    check_results   JSONB NOT NULL DEFAULT '{}'::jsonb,  -- per-control results
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until     TIMESTAMPTZ,
    CONSTRAINT uq_cert_tenant_framework UNIQUE (tenant_id, framework)
);

-- --------------------------------------------------------
-- Table: whitelabel_configs
-- Per-tenant white-label branding
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS whitelabel_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    product_name    TEXT NOT NULL DEFAULT 'AgentVerse',
    logo_url        TEXT,
    favicon_url     TEXT,
    primary_color   TEXT NOT NULL DEFAULT '#6366f1',
    secondary_color TEXT NOT NULL DEFAULT '#8b5cf6',
    custom_css      TEXT,
    support_email   TEXT,
    support_url     TEXT,
    privacy_policy_url TEXT,
    terms_url       TEXT,
    custom_domain   TEXT UNIQUE,
    hide_agentverse_branding BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE whitelabel_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY whitelabel_isolation ON whitelabel_configs
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

COMMIT;
```

### 3.2 Alembic Migration

```python
# agent-verse-backend/app/db/migrations/versions/0060_enterprise_features.py
"""enterprise_contracts, saml_configs, scim_configs, compliance_certifications, whitelabel

Revision ID: 0060
Revises: 0059
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMPTZ

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name, columns in [
        ("enterprise_contracts", [
            sa.Column("id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID(as_uuid=True),
                      sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("contract_type", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="'pending'"),
            sa.Column("version", sa.Text(), nullable=False, server_default="'1.0'"),
            sa.Column("signed_by_name", sa.Text()),
            sa.Column("signed_by_email", sa.Text()),
            sa.Column("signed_at", TIMESTAMPTZ()),
            sa.Column("expires_at", TIMESTAMPTZ()),
            sa.Column("document_url", sa.Text()),
            sa.Column("document_hash", sa.Text()),
            sa.Column("metadata", JSONB(), nullable=False, server_default="'{}'"),
            sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        ]),
        ("saml_configs", [
            sa.Column("id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID(as_uuid=True),
                      sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("idp_entity_id", sa.Text(), nullable=False),
            sa.Column("idp_sso_url", sa.Text(), nullable=False),
            sa.Column("idp_cert", sa.Text(), nullable=False),
            sa.Column("sp_entity_id", sa.Text(), nullable=False),
            sa.Column("attribute_mapping", JSONB(), nullable=False, server_default="'{}'"),
            sa.Column("default_role", sa.Text(), nullable=False, server_default="'viewer'"),
            sa.Column("jit_provisioning", sa.Boolean(), nullable=False, server_default="TRUE"),
            sa.Column("force_authn", sa.Boolean(), nullable=False, server_default="FALSE"),
            sa.Column("name_id_format", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
            sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        ]),
        ("scim_configs", [
            sa.Column("id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID(as_uuid=True),
                      sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("bearer_token_hash", sa.Text(), nullable=False),
            sa.Column("bearer_token_prefix", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
            sa.Column("allow_user_create", sa.Boolean(), nullable=False, server_default="TRUE"),
            sa.Column("allow_user_update", sa.Boolean(), nullable=False, server_default="TRUE"),
            sa.Column("allow_user_delete", sa.Boolean(), nullable=False, server_default="FALSE"),
            sa.Column("allow_group_sync", sa.Boolean(), nullable=False, server_default="TRUE"),
            sa.Column("default_role", sa.Text(), nullable=False, server_default="'viewer'"),
            sa.Column("group_role_map", JSONB(), nullable=False, server_default="'{}'"),
            sa.Column("last_sync_at", TIMESTAMPTZ()),
            sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        ]),
    ]:
        op.create_table(table_name, *columns)


def downgrade() -> None:
    for t in ["whitelabel_configs", "compliance_certifications", "scim_configs",
              "saml_configs", "enterprise_contracts"]:
        op.drop_table(t)
```

### 3.3 API Endpoints

**GET /api/enterprise/compliance/{framework}** — Dynamic compliance status
```json
{
  "framework": "hipaa",
  "status": "partial",
  "controls": {
    "baa_signed": { "pass": true, "signed_at": "2026-01-15T..." },
    "phi_access_logging": { "pass": true },
    "minimum_necessary": { "pass": false, "note": "HITL policy not configured for PHI endpoints" },
    "workforce_training": { "pass": false, "note": "No training expiry tracked" }
  },
  "computed_at": "2026-06-28T00:00:00Z"
}
```

**POST /api/enterprise/compliance/{framework}/check** — Re-run compliance checks

**GET /api/enterprise/contracts** — List signed contracts

**POST /api/enterprise/contracts/{type}/sign** — BAA/DPA signing flow
```json
{
  "signer_name": "Jane Doe",
  "signer_email": "jane@hospital.org",
  "signer_title": "CTO"
}
```
Response: `{ "contract_id": "uuid", "document_url": "https://...", "signed_at": "..." }`

#### SAML 2.0

**GET /api/enterprise/saml/metadata** — Returns SP metadata XML for IdP configuration

**POST /api/enterprise/saml/configure**
```json
{
  "idp_entity_id": "https://idp.company.com/entity",
  "idp_sso_url": "https://idp.company.com/sso",
  "idp_cert": "-----BEGIN CERTIFICATE-----\n...",
  "attribute_mapping": {
    "email": "urn:oid:0.9.2342.19200300.100.1.3",
    "first_name": "urn:oid:2.5.4.42",
    "department": "urn:oid:2.5.4.11"
  },
  "default_role": "developer",
  "jit_provisioning": true
}
```

**GET /api/enterprise/saml/login** — Initiates SAML SSO redirect

**POST /api/enterprise/saml/acs** — Assertion Consumer Service (SAML callback)

**POST /api/enterprise/saml/slo** — Single Logout Service

#### SCIM 2.0

**GET /api/scim/v2/Users** — SCIM user listing (paginated)

**GET /api/scim/v2/Users/{scim_id}** — Get user

**POST /api/scim/v2/Users** — Create user (from IdP provisioning)

**PUT /api/scim/v2/Users/{scim_id}** — Full user replacement

**PATCH /api/scim/v2/Users/{scim_id}** — Partial update (e.g., deactivate)

**DELETE /api/scim/v2/Users/{scim_id}** — Deprovision user

**GET /api/scim/v2/Groups** — Sync groups → roles

**POST /api/scim/v2/Groups**

**PATCH /api/scim/v2/Groups/{scim_id}** — Update group membership

#### GDPR

**POST /api/enterprise/gdpr/export** — **FIXED: no 500-record cap**
```json
{ "subject_id": "user-uuid", "format": "json" }
```
Response 202: `{ "export_id": "uuid", "estimated_records": 4720, "download_url_expires_at": "..." }`

Implementation requires streaming export (no loading all records in memory):
```python
async def stream_gdpr_export(subject_id: str, db):
    # Stream all goals, audit events, cost records, agent configs in batches of 1000
    # No cap — export ALL records as required by GDPR Art. 20
    async with db() as session:
        offset = 0
        batch_size = 1000
        while True:
            batch = await session.execute(
                select(Goal).where(Goal.created_by == subject_id)
                .offset(offset).limit(batch_size)
            )
            rows = batch.fetchall()
            if not rows:
                break
            for row in rows:
                yield row
            offset += batch_size
```

**POST /api/enterprise/gdpr/erasure** — Right to erasure (deletes non-held data)

**GET /api/enterprise/gdpr/exports/{export_id}** — Download URL when ready

#### White-label

**GET /api/enterprise/whitelabel** — Current branding config

**PATCH /api/enterprise/whitelabel**
```json
{
  "product_name": "Acme AI Platform",
  "primary_color": "#1a73e8",
  "logo_url": "https://cdn.acme.com/logo.png",
  "custom_domain": "agents.acme.com"
}
```

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/enterprise/compliance.py
"""
Dynamic compliance checking — replaces hardcoded gdpr_compliant=True.

FIX: compliance.get_data_residency() previously returned:
  { gdpr_compliant: True, ... }
regardless of actual configuration.

This implementation computes actual compliance status from DB state.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Optional
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger(__name__)


class ComplianceChecker:
    """
    Dynamically evaluates compliance status for a tenant.
    Never returns hardcoded True — every claim is verified.
    """

    def __init__(self, db_factory) -> None:
        self._db = db_factory

    async def check_hipaa(self, tenant_id: str) -> dict[str, Any]:
        """HIPAA compliance: all required controls must pass."""
        controls: dict[str, dict] = {}

        async with self._db() as db:
            # 1. BAA must be signed
            baa = await self._get_contract(db, tenant_id, "baa")
            controls["baa_signed"] = {
                "pass": baa is not None and baa["status"] == "signed",
                "signed_at": baa["signed_at"] if baa else None,
                "note": None if baa else "Business Associate Agreement not signed",
            }

            # 2. PHI access logging (audit) must be active
            audit_active = await self._check_audit_active(db, tenant_id)
            controls["phi_access_logging"] = {
                "pass": audit_active,
                "note": None if audit_active else "Audit logging not active",
            }

            # 3. Minimum necessary: HITL policy for PHI tools must exist
            hitl_policy = await self._check_phi_hitl_policy(db, tenant_id)
            controls["minimum_necessary"] = {
                "pass": hitl_policy,
                "note": None if hitl_policy else "HITL approval policy for PHI endpoints not configured",
            }

            # 4. Workforce training tracking
            training = await self._check_training_tracking(db, tenant_id)
            controls["workforce_training"] = {
                "pass": training,
                "note": None if training else "HIPAA workforce training expiry not tracked",
            }

            # 5. Encryption at rest (infrastructure level — check tenant tier)
            enc = await self._check_encryption_tier(db, tenant_id)
            controls["encryption_at_rest"] = {
                "pass": enc,
                "note": None if enc else "Enterprise tier required for encryption at rest guarantee",
            }

        passed = sum(1 for c in controls.values() if c["pass"])
        total = len(controls)

        if passed == total:
            status = "compliant"
        elif passed >= total * 0.8:
            status = "partial"
        else:
            status = "non_compliant"

        return {
            "framework": "hipaa",
            "status": status,
            "controls": controls,
            "passed_count": passed,
            "total_count": total,
        }

    async def check_gdpr(self, tenant_id: str) -> dict[str, Any]:
        """GDPR compliance: DPA must be signed, data residency must be EU."""
        controls: dict[str, dict] = {}

        async with self._db() as db:
            # 1. Data Processing Agreement signed
            dpa = await self._get_contract(db, tenant_id, "dpa")
            controls["dpa_signed"] = {
                "pass": dpa is not None and dpa["status"] == "signed",
                "note": None if dpa else "Data Processing Agreement not signed",
            }

            # 2. Data residency configured to EU region
            residency = await self._get_data_residency_region(db, tenant_id)
            eu_regions = {"eu-west-1", "eu-central-1", "eu-north-1", "eu-west-2"}
            controls["eu_data_residency"] = {
                "pass": residency in eu_regions,
                "region": residency,
                "note": None if residency in eu_regions else f"Region {residency} is not EU; GDPR requires EU data residency",
            }

            # 3. Right to erasure implemented (retention policy configured)
            retention = await self._check_retention_policy(db, tenant_id)
            controls["retention_policy"] = {
                "pass": retention,
                "note": None if retention else "Data retention policy not configured",
            }

            # 4. Export capability (right to portability)
            controls["data_portability"] = {
                "pass": True,  # Platform always supports GDPR export
                "note": None,
            }

            # 5. Consent tracking (if applicable)
            controls["consent_management"] = {
                "pass": True,  # Implied by BAA/DPA signing
                "note": None,
            }

        passed = sum(1 for c in controls.values() if c["pass"])
        total = len(controls)

        if passed == total:
            status = "compliant"
        elif passed >= 3:
            status = "partial"
        else:
            status = "non_compliant"

        return {
            "framework": "gdpr",
            "status": status,
            "controls": controls,
        }

    async def _get_contract(self, db, tenant_id: str, contract_type: str):
        from sqlalchemy import select
        from app.db.models.enterprise import EnterpriseContract
        result = await db.execute(
            select(EnterpriseContract).where(
                EnterpriseContract.tenant_id == UUID(tenant_id),
                EnterpriseContract.contract_type == contract_type,
                EnterpriseContract.status == "signed",
            ).order_by(EnterpriseContract.signed_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        return {"status": row.status, "signed_at": str(row.signed_at)} if row else None

    async def _check_audit_active(self, db, tenant_id: str) -> bool:
        from sqlalchemy import select, text
        result = await db.execute(
            text("SELECT COUNT(*) FROM audit_events WHERE tenant_id = :tid LIMIT 1"),
            {"tid": tenant_id},
        )
        return (result.scalar() or 0) >= 1

    async def _check_phi_hitl_policy(self, db, tenant_id: str) -> bool:
        from sqlalchemy import select, text
        result = await db.execute(
            text("""
                SELECT COUNT(*) FROM policy_versions
                WHERE tenant_id = :tid
                  AND is_active = TRUE
                  AND rules::text ILIKE '%patient%'
                  AND rules::text ILIKE '%hitl%'
            """),
            {"tid": tenant_id},
        )
        return (result.scalar() or 0) >= 1

    async def _check_training_tracking(self, db, tenant_id: str) -> bool:
        # Check if tenant has configured HIPAA training expiry in tenant_settings
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT settings->>'hipaa_training_enabled' FROM tenant_settings WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        row = result.fetchone()
        return row is not None and row[0] == "true"

    async def _check_encryption_tier(self, db, tenant_id: str) -> bool:
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT plan FROM tenants WHERE id = :tid"),
            {"tid": tenant_id},
        )
        row = result.fetchone()
        return row is not None and row[0] in ("enterprise", "hipaa")

    async def _get_data_residency_region(self, db, tenant_id: str) -> str:
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT settings->>'data_region' FROM tenant_settings WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        row = result.fetchone()
        return (row[0] if row and row[0] else "us-east-1")

    async def _check_retention_policy(self, db, tenant_id: str) -> bool:
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT settings->>'retention_days' FROM tenant_settings WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        row = result.fetchone()
        return row is not None and row[0] is not None


# ---------------------------------------------------------------------------
# NIST-compliant API key generation
# ---------------------------------------------------------------------------

def generate_api_key(prefix: str = "av_live") -> tuple[str, str, str]:
    """
    FIX: Was using uuid4() (122 bits, not recommended for tokens).
    Now uses secrets.token_urlsafe(32) = 256 bits, NIST SP 800-131A compliant.

    Returns: (full_key, key_prefix, key_hash)
    """
    random_part = secrets.token_urlsafe(32)  # 256-bit entropy, URL-safe base64
    full_key = f"{prefix}_{random_part}"
    key_prefix = full_key[:16]
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_prefix, key_hash


# ---------------------------------------------------------------------------
# SAML 2.0 integration
# ---------------------------------------------------------------------------

class SAMLService:
    """
    SAML 2.0 SSO integration using python3-saml.

    Flow:
      1. GET /api/enterprise/saml/login → redirect to IdP SSO URL
      2. IdP authenticates user → POST to /api/enterprise/saml/acs
      3. ACS validates assertion, extracts attributes, JIT-provisions user
      4. Returns session JWT
    """

    def __init__(self, tenant_config: dict) -> None:
        self._config = tenant_config

    def get_sp_metadata(self, sp_entity_id: str, acs_url: str) -> str:
        """Returns SP metadata XML for IdP configuration."""
        try:
            from onelogin.saml2.metadata import OneLogin_Saml2_Metadata
            settings = self._build_settings(sp_entity_id, acs_url)
            metadata = OneLogin_Saml2_Metadata.builder(settings["sp"])
            return metadata
        except ImportError:
            # Return minimal SP metadata XML
            return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    entityID="{sp_entity_id}">
  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true"
      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="{acs_url}" index="1"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>"""

    def initiate_sso(self, sp_entity_id: str, acs_url: str) -> str:
        """Returns the redirect URL for the IdP SSO endpoint."""
        try:
            from onelogin.saml2.auth import OneLogin_Saml2_Auth
            settings = self._build_settings(sp_entity_id, acs_url)
            auth = OneLogin_Saml2_Auth(request_data={}, old_settings=settings)
            return auth.login()
        except ImportError:
            return self._config["idp_sso_url"]

    def process_acs(
        self,
        sp_entity_id: str,
        acs_url: str,
        saml_response: str,
    ) -> dict[str, Any]:
        """
        Validates SAML assertion, extracts user attributes.
        Returns {"email": ..., "attributes": {...}, "name_id": ...}
        """
        try:
            from onelogin.saml2.auth import OneLogin_Saml2_Auth
            import base64

            settings = self._build_settings(sp_entity_id, acs_url)
            request_data = {
                "post_data": {"SAMLResponse": saml_response},
                "https": "on",
                "http_host": acs_url.split("/")[2],
                "script_name": "/api/enterprise/saml/acs",
            }
            auth = OneLogin_Saml2_Auth(request_data, old_settings=settings)
            auth.process_response()

            if not auth.is_authenticated():
                errors = auth.get_errors()
                raise ValueError(f"SAML authentication failed: {errors}")

            attrs = auth.get_attributes()
            name_id = auth.get_nameid()

            # Map attributes using configured mapping
            mapping = self._config.get("attribute_mapping", {})
            email = self._extract_attr(attrs, mapping.get("email", "email"))
            first_name = self._extract_attr(attrs, mapping.get("first_name", "firstName"))
            last_name = self._extract_attr(attrs, mapping.get("last_name", "lastName"))
            department = self._extract_attr(attrs, mapping.get("department", "department"))

            return {
                "email": email or name_id,
                "first_name": first_name,
                "last_name": last_name,
                "department": department,
                "name_id": name_id,
                "attributes": attrs,
            }

        except ImportError:
            raise RuntimeError("python3-saml not installed. Install: pip install python3-saml")

    @staticmethod
    def _extract_attr(attrs: dict, key: str) -> Optional[str]:
        values = attrs.get(key, [])
        return values[0] if values else None

    def _build_settings(self, sp_entity_id: str, acs_url: str) -> dict:
        return {
            "sp": {
                "entityId": sp_entity_id,
                "assertionConsumerService": {
                    "url": acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "NameIDFormat": self._config.get(
                    "name_id_format",
                    "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
                ),
            },
            "idp": {
                "entityId": self._config["idp_entity_id"],
                "singleSignOnService": {
                    "url": self._config["idp_sso_url"],
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self._config["idp_cert"],
            },
            "security": {
                "wantAssertionsSigned": True,
                "requestedAuthnContext": False,
            },
        }


# ---------------------------------------------------------------------------
# SCIM 2.0 handler
# ---------------------------------------------------------------------------

class SCIMHandler:
    """
    Handles SCIM 2.0 User and Group provisioning.
    Implements RFC 7644 (SCIM Protocol).
    """

    SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
    SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"

    def __init__(self, tenant_id: str, config: dict, db_factory) -> None:
        self._tenant_id = tenant_id
        self._config = config
        self._db = db_factory

    async def list_users(self, start_index: int = 1, count: int = 100) -> dict:
        async with self._db() as db:
            from sqlalchemy import select
            from app.db.models.user import User
            result = await db.execute(
                select(User).where(User.tenant_id == UUID(self._tenant_id))
                .offset(start_index - 1).limit(count)
            )
            users = result.scalars().all()
            total = await db.scalar(
                select(__import__("sqlalchemy").func.count())
                .where(User.tenant_id == UUID(self._tenant_id))
            )

        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": count,
            "Resources": [self._user_to_scim(u) for u in users],
        }

    async def create_user(self, scim_data: dict) -> dict:
        if not self._config.get("allow_user_create", True):
            raise PermissionError("SCIM user creation is disabled for this tenant")

        email = scim_data.get("userName") or (scim_data.get("emails") or [{}])[0].get("value")
        if not email:
            raise ValueError("userName or emails[0].value required")

        name = scim_data.get("name", {})
        display_name = f"{name.get('givenName', '')} {name.get('familyName', '')}".strip()

        # Map group memberships to roles
        groups = scim_data.get("groups", [])
        role = self._map_groups_to_role(groups)

        async with self._db() as db:
            from app.db.models.user import User
            user = User(
                tenant_id=UUID(self._tenant_id),
                email=email,
                display_name=display_name or email,
                role=role,
                scim_id=scim_data.get("externalId") or scim_data.get("id"),
                is_active=scim_data.get("active", True),
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        return self._user_to_scim(user)

    async def update_user(self, scim_id: str, scim_data: dict, partial: bool = False) -> dict:
        async with self._db() as db:
            from sqlalchemy import select, update
            from app.db.models.user import User

            result = await db.execute(
                select(User).where(
                    User.tenant_id == UUID(self._tenant_id),
                    User.scim_id == scim_id,
                )
            )
            user = result.scalar_one_or_none()
            if not user:
                raise ValueError(f"User {scim_id} not found")

            if partial:
                # PATCH: apply operations
                for op in scim_data.get("Operations", []):
                    if op.get("op") == "replace" and op.get("path") == "active":
                        user.is_active = op["value"]
            else:
                # PUT: full replacement
                active = scim_data.get("active", True)
                if not active and not self._config.get("allow_user_delete", False):
                    raise PermissionError("SCIM user deactivation is disabled")
                user.is_active = active

            await db.commit()
            return self._user_to_scim(user)

    def _map_groups_to_role(self, groups: list) -> str:
        group_role_map = self._config.get("group_role_map", {})
        for group in groups:
            group_name = group.get("display", "")
            if group_name in group_role_map:
                return group_role_map[group_name]
        return self._config.get("default_role", "viewer")

    @staticmethod
    def _user_to_scim(user) -> dict:
        return {
            "schemas": [SCIMHandler.SCIM_USER_SCHEMA],
            "id": str(user.id),
            "externalId": user.scim_id,
            "userName": user.email,
            "displayName": user.display_name,
            "active": user.is_active,
            "emails": [{"value": user.email, "primary": True}],
            "meta": {
                "resourceType": "User",
                "created": str(user.created_at),
                "lastModified": str(user.updated_at) if hasattr(user, "updated_at") else None,
            },
        }
```

### 3.5 main.py Wiring

```python
from app.enterprise.compliance import ComplianceChecker
from app.enterprise.router import router as enterprise_router
from app.enterprise.scim_router import router as scim_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app.state.compliance_checker = ComplianceChecker(app.state.db_session_factory)
    app.include_router(enterprise_router, prefix="/api/enterprise", tags=["Enterprise"])
    app.include_router(scim_router, prefix="/scim/v2", tags=["SCIM 2.0"])
    return app
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar | Description |
|-------|---------|-------------|
| `/enterprise` | Enterprise | Compliance dashboard |
| `/enterprise/compliance` | Enterprise → Compliance | Per-framework status cards |
| `/enterprise/contracts` | Enterprise → Contracts | BAA/DPA management |
| `/enterprise/sso` | Enterprise → SSO | SAML configuration |
| `/enterprise/scim` | Enterprise → Provisioning | SCIM configuration |
| `/enterprise/gdpr` | Enterprise → GDPR | Data export and erasure |
| `/enterprise/whitelabel` | Enterprise → Branding | White-label configuration |

### 4.2 TypeScript Interfaces

```typescript
// src/features/enterprise/types.ts

export interface ComplianceStatus {
  framework: 'hipaa' | 'gdpr' | 'sox' | 'pci_dss' | 'soc2';
  status: 'not_configured' | 'partial' | 'compliant' | 'non_compliant';
  controls: Record<string, ControlResult>;
  passedCount: number;
  totalCount: number;
  computedAt: string;
}

export interface ControlResult {
  pass: boolean;
  note: string | null;
  documentUrl?: string | null;
  signedAt?: string | null;
}

export interface SAMLConfig {
  idpEntityId: string;
  idpSsoUrl: string;
  spEntityId: string;
  attributeMapping: Record<string, string>;
  defaultRole: string;
  jitProvisioning: boolean;
  isActive: boolean;
}

export interface SCIMConfig {
  bearerTokenPrefix: string;
  isActive: boolean;
  allowUserCreate: boolean;
  allowUserUpdate: boolean;
  allowUserDelete: boolean;
  allowGroupSync: boolean;
  defaultRole: string;
  groupRoleMap: Record<string, string>;
  lastSyncAt: string | null;
}

export interface WhitelabelConfig {
  productName: string;
  logoUrl: string | null;
  primaryColor: string;
  secondaryColor: string;
  customDomain: string | null;
  hideAgentverseBranding: boolean;
}

export interface GDPRExport {
  exportId: string;
  subjectId: string;
  estimatedRecords: number;
  status: 'pending' | 'generating' | 'ready' | 'expired';
  downloadUrl: string | null;
  downloadUrlExpiresAt: string | null;
  createdAt: string;
}
```

### 4.3 Animation Specs

```css
/* src/features/enterprise/enterprise-animations.css */

/* Compliance status card reveal */
@keyframes complianceCardReveal {
  from { opacity: 0; transform: scale(0.95) translateY(8px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}

/* Control check tick */
@keyframes controlCheckTick {
  0%   { stroke-dashoffset: 24; opacity: 0; }
  50%  { opacity: 1; }
  100% { stroke-dashoffset: 0; opacity: 1; }
}

/* Control fail X */
@keyframes controlFailX {
  0%   { opacity: 0; transform: scale(0.5); }
  100% { opacity: 1; transform: scale(1); }
}

/* SAML test flow progress */
@keyframes samlFlowStep {
  from { opacity: 0; transform: translateX(20px); }
  to   { opacity: 1; transform: translateX(0); }
}

/* Compliance percentage ring */
@keyframes complianceRing {
  from { stroke-dashoffset: 226; }
  to   { stroke-dashoffset: var(--compliance-offset); }
}

/* GDPR export countdown */
@keyframes exportCountdown {
  from { width: 100%; }
  to   { width: 0%; }
}

.compliance-card          { animation: complianceCardReveal 0.35s ease-out both; }
.control-check            { animation: controlCheckTick 0.4s ease-out both; }
.control-fail             { animation: controlFailX 0.25s ease-out both; }
.saml-flow-step           { animation: samlFlowStep 0.25s ease-out both; }
.compliance-ring          { animation: complianceRing 1s cubic-bezier(0.4, 0, 0.2, 1) both; }
```

### 4.4 Dark Mode & Mobile

```css
.compliance-card { background: var(--color-surface-1); border: 1px solid var(--color-border-default); }
.status-compliant     { color: var(--color-success-emphasis); background: var(--color-success-subtle); }
.status-partial       { color: var(--color-warning-emphasis); background: var(--color-warning-subtle); }
.status-non-compliant { color: var(--color-danger-emphasis);  background: var(--color-danger-subtle); }
.contract-signed      { color: var(--color-success-emphasis); }
.contract-pending     { color: var(--color-attention-emphasis); }

@media (max-width: 640px) {
  .compliance-grid     { grid-template-columns: 1fr; }
  .saml-config-form    { max-width: 100%; }
  .cert-table          { display: block; overflow-x: auto; }
}
```

---

## 5. Scale Architecture

| Challenge | Solution |
|-----------|----------|
| GDPR export for 1M+ records | Streaming cursor; S3 multipart upload; no in-memory accumulation |
| SAML token validation | python3-saml; cert pinned per tenant; replay protection via assertion ID cache in Redis |
| SCIM rate limiting | SCIM endpoints rate-limited per token; 1000 operations/minute |
| Compliance check caching | Results cached 5 min in Redis per tenant/framework; invalidated on contract sign |
| White-label CDN | Custom domain → CloudFront → custom_domain lookup in DB → tenant branding |

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/enterprise/test_compliance.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.enterprise.compliance import ComplianceChecker, generate_api_key


class TestAPIKeyGeneration:
    def test_generates_nist_compliant_key(self):
        key, prefix, key_hash = generate_api_key("av_live")
        assert key.startswith("av_live_")
        # 256 bits of entropy = 32 bytes = 43 base64url chars
        random_part = key[len("av_live_"):]
        assert len(random_part) >= 43

    def test_prefix_is_16_chars(self):
        key, prefix, _ = generate_api_key("av_live")
        assert len(prefix) == 16
        assert key.startswith(prefix)

    def test_key_hash_is_sha256(self):
        import hashlib
        key, prefix, key_hash = generate_api_key("av_live")
        assert key_hash == hashlib.sha256(key.encode()).hexdigest()

    def test_two_keys_are_different(self):
        key1, _, _ = generate_api_key()
        key2, _, _ = generate_api_key()
        assert key1 != key2


class TestComplianceChecker:
    def _make_checker(self, settings_data=None):
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        # Default: baa signed, audit active, no PHI policy, no training
        def execute_side_effect(query, *args, **kwargs):
            q = str(query)
            mock_result = MagicMock()
            if "enterprise_contracts" in q or "EnterpriseContract" in q:
                mock_row = MagicMock(status="signed", signed_at="2026-01-15")
                mock_result.scalar_one_or_none = lambda: mock_row
            elif "audit_events" in q:
                mock_result.scalar = lambda: 100
            elif "policy_versions" in q:
                mock_result.scalar = lambda: 0  # no PHI HITL policy
            elif "tenant_settings" in q:
                mock_result.fetchone = lambda: None
            elif "tenants" in q:
                mock_result.fetchone = lambda: MagicMock(__getitem__=lambda s, i: "enterprise")
            else:
                mock_result.scalar_one_or_none = lambda: None
                mock_result.fetchone = lambda: None
                mock_result.scalar = lambda: 0
            return mock_result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.scalar = AsyncMock(return_value=None)
        return ComplianceChecker(lambda: mock_db)

    @pytest.mark.asyncio
    async def test_hipaa_partial_without_phi_policy(self):
        checker = self._make_checker()
        result = await checker.check_hipaa("tenant-1")
        # BAA signed, audit active, but no PHI HITL policy → partial
        assert result["status"] in ("partial", "non_compliant")
        assert result["controls"]["baa_signed"]["pass"] is True
        assert result["controls"]["minimum_necessary"]["pass"] is False

    @pytest.mark.asyncio
    async def test_gdpr_compliance_checks_contract(self):
        checker = self._make_checker()
        result = await checker.check_gdpr("tenant-1")
        assert "dpa_signed" in result["controls"]
        # DPA is signed in our mock
        assert result["controls"]["dpa_signed"]["pass"] is True

    @pytest.mark.asyncio
    async def test_not_hardcoded_true(self):
        """
        FIX TEST: Verifies compliance is never hardcoded True.
        """
        checker = self._make_checker()
        result = await checker.check_hipaa("tenant-1")
        # At least one control must be False (minimum_necessary not configured)
        failing_controls = [k for k, v in result["controls"].items() if not v["pass"]]
        assert len(failing_controls) > 0, "Compliance should NOT be hardcoded True"
        assert result["status"] != "compliant"


class TestSCIMHandler:
    @pytest.mark.asyncio
    async def test_create_user_maps_groups_to_role(self):
        from app.enterprise.compliance import SCIMHandler
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        created_users = []

        class MockUser:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
                self.id = MagicMock()
                self.scim_id = kwargs.get("scim_id")
                self.is_active = kwargs.get("is_active", True)
                self.created_at = "2026-06-28"

        mock_db.add = MagicMock(side_effect=lambda u: created_users.append(u))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        config = {
            "allow_user_create": True,
            "default_role": "viewer",
            "group_role_map": {"Engineering": "developer", "Compliance": "viewer"},
        }

        from unittest.mock import patch
        with patch("app.enterprise.compliance.User", MockUser):
            handler = SCIMHandler("tenant-1", config, lambda: mock_db)

            scim_user = {
                "userName": "jdoe@example.com",
                "name": {"givenName": "John", "familyName": "Doe"},
                "groups": [{"display": "Engineering"}],
                "active": True,
            }

            result = await handler.create_user(scim_user)
            assert len(created_users) == 1
            assert created_users[0].role == "developer"

    @pytest.mark.asyncio
    async def test_create_user_blocked_when_disabled(self):
        from app.enterprise.compliance import SCIMHandler
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        config = {"allow_user_create": False}
        handler = SCIMHandler("tenant-1", config, lambda: mock_db)

        with pytest.raises(PermissionError, match="SCIM user creation is disabled"):
            await handler.create_user({"userName": "test@example.com"})
```

---

## 7. Domain Extensibility

### Healthcare (HIPAA)
```python
# Full HIPAA control checklist:
#   + Administrative safeguards: workforce training, assigned security responsibility
#   + Physical safeguards: facility access, workstation use policies
#   + Technical safeguards: audit controls, integrity, transmission security
# BAA template generator: auto-generate appropriate BAA text for healthcare tenants
# Minimum necessary implementation: HITL gate on all PHI tool calls
# PHI Access Log: every data access creates HIPAA-formatted audit entry
```

### Legal (eDiscovery)
```python
# Legal hold integration: enterprise_contracts includes litigation_hold_order type
# Chain of custody: audit trail formatted for court admissibility
# Privilege log: automated identification of potentially privileged documents
# Client portal: white-label per-matter portal for external client access
```

### Finance (SOX/FINRA)
```python
# SOX controls: journal entry approval chains, segregation of duties
# FINRA record retention: 7-year retention policy auto-configured on sign-up
# White-label for broker-dealers: "Acme Wealth Management AI" with custom T&C
# Pre-clearance integration: SCIM sync with compliance management systems
```

### Education
```python
# FERPA compliance: student record access audit, parent access rights
# COPPA: no data collection for users under 13 (verified via SAML age claim)
# District SSO: SAML integration with common education IdPs (Clever, ClassLink)
```

### E-commerce (PCI DSS)
```python
# PCI DSS compliance checker: check for cardholder data environment controls
# White-label Shopify app: SCIM sync with Shopify staff accounts
# GDPR + CCPA: combined privacy export for EU and California residents
```

---

## AMENDMENTS — Critical Fixes

### Amendment 8.1 — Fix missing tables in upgrade() function

```python
# These two tables appear in DDL but are NOT in the upgrade() function:
# compliance_certifications and whitelabel_configs

def upgrade() -> None:
    # ... existing tables ...
    
    # ADD THESE MISSING TABLES:
    op.execute("""
        CREATE TABLE IF NOT EXISTS compliance_certifications (
            id                TEXT PRIMARY KEY,
            tenant_id         TEXT NOT NULL,
            certification_type TEXT NOT NULL,  -- 'soc2_type2'|'hipaa'|'gdpr'|'iso27001'|'pci_dss'
            status            TEXT NOT NULL DEFAULT 'not_certified',
            issued_at         TIMESTAMPTZ,
            expires_at        TIMESTAMPTZ,
            certificate_url   TEXT,
            certified_by      TEXT,
            scope_description TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_compliance_certs_tenant ON compliance_certifications(tenant_id);
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS whitelabel_configs (
            tenant_id         TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            brand_name        TEXT NOT NULL DEFAULT 'AgentVerse',
            logo_url          TEXT,
            primary_color     TEXT NOT NULL DEFAULT '#3B82F6',
            custom_domain     TEXT UNIQUE,
            custom_email_from TEXT,
            hide_branding     BOOLEAN NOT NULL DEFAULT FALSE,
            terms_url         TEXT,
            privacy_url       TEXT,
            support_email     TEXT,
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # Add to downgrade():
    # op.execute("DROP TABLE IF EXISTS whitelabel_configs CASCADE")
    # op.execute("DROP TABLE IF EXISTS compliance_certifications CASCADE")
```

### Amendment 8.2 — Add SCIM bearer token authentication

```python
# SCIM endpoints must authenticate via bearer token:
def _require_scim_auth(request: Request) -> str:
    """Authenticate SCIM requests via pre-provisioned bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "SCIM requires Bearer token authentication")
    token = auth[7:].strip()
    # Hash and lookup:
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # DB lookup: SELECT tenant_id FROM scim_tokens WHERE token_hash = :hash AND revoked_at IS NULL
    # (Add scim_tokens table to migration)
    tenant_id = _lookup_scim_token(token_hash, request)
    if not tenant_id:
        raise HTTPException(401, "Invalid SCIM bearer token")
    return tenant_id

# Add scim_tokens table to migration:
op.execute("""
    CREATE TABLE IF NOT EXISTS scim_tokens (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        revoked_at TIMESTAMPTZ
    );
""")
```

### Amendment 8.3 — Fix check_gdpr() hardcoded True values

```python
# BEFORE (re-introduces the bug):
# controls["data_portability"] = {"pass": True}
# controls["consent_management"] = {"pass": True}

# AFTER (checks real DB records):
async def check_gdpr(self, tenant_id: str) -> ComplianceCheckResult:
    controls = {}
    # Data portability: check if GDPR export was successfully run in last 90 days
    async with self._db() as session:
        recent_export = (await session.execute(_t("""
            SELECT COUNT(*) FROM gdpr_export_jobs
            WHERE tenant_id = :tid AND status = 'completed' AND completed_at > NOW() - INTERVAL '90 days'
        """), {"tid": tenant_id})).scalar()
        controls["data_portability"] = {"pass": recent_export > 0, "detail": f"{recent_export} exports in last 90 days"}

        # Consent management: check if consent records exist
        consent_count = (await session.execute(_t(
            "SELECT COUNT(*) FROM consent_records WHERE tenant_id = :tid"
        ), {"tid": tenant_id})).scalar()
        controls["consent_management"] = {"pass": consent_count > 0, "detail": f"{consent_count} consent records"}

        # DPA signed: check enterprise_contracts
        dpa_signed = (await session.execute(_t(
            "SELECT COUNT(*) FROM enterprise_contracts WHERE tenant_id = :tid AND contract_type = 'dpa' AND signed_at IS NOT NULL"
        ), {"tid": tenant_id})).scalar()
        controls["dpa_signed"] = {"pass": dpa_signed > 0, "detail": "Data Processing Agreement" if dpa_signed else "DPA not signed"}
    return ComplianceCheckResult(framework="gdpr", controls=controls, overall_pass=all(c["pass"] for c in controls.values()))
```

### Amendment 8.4 — Add SAML replay protection + fix python3-saml boolean

```python
# SAML replay protection using Redis assertion ID cache:
async def _check_saml_replay(self, assertion_id: str, redis) -> bool:
    """Return True if assertion is a replay (already seen)."""
    key = f"saml_assertion:{assertion_id}"
    is_replay = await redis.exists(key)
    if not is_replay:
        await redis.setex(key, 3600, "used")  # 1-hour replay window
    return bool(is_replay)

# Call in process_acs():
if await self._check_saml_replay(assertion_id, self._redis):
    raise HTTPException(401, "SAML assertion replay detected")

# Fix python3-saml https boolean (version-compatible):
import sys
if sys.version_info >= (3, 11):
    request_data = {"https": True, ...}  # newer python3-saml accepts bool
else:
    request_data = {"https": "on", ...}  # older versions need string
```

### Amendment 8.5 — App.tsx routes + Sidebar + prefers-reduced-motion + toast

```typescript
// App.tsx: EnterprisePage already exists — ensure lazy
// Additional routes:
const SAMLCallbackPage = lazy(() => import("@/features/auth/SAMLCallbackPage").then(m => ({default: m.SAMLCallbackPage})));
// Public route (outside RequireAuth): <Route path="/auth/saml/callback" element={<SAMLCallbackPage />} />
// Sidebar: EnterprisePage already linked

// prefers-reduced-motion:
@media (prefers-reduced-motion: reduce) {
  .compliance-badge-flip, .saml-step-checkmark, .data-flow-arrow, .sla-gauge-needle {
    animation: none !important; transition: none !important;
  }
}

// Toast notifications:
// uploadContract onSuccess: toast({kind:"success", message:"Contract uploaded and stored securely"})
// updateWhiteLabel onSuccess: toast({kind:"success", message:"White-label configuration saved"})
// requestDataExport onSuccess: toast({kind:"info", message:"GDPR export started — you'll receive a download link when ready"})
// deleteAllData → ConfirmModal variant="danger" title="Delete all data?" description="This permanently removes all goals, agents, knowledge, and audit logs. Type 'DELETE MY DATA' to confirm." + toast

// Empty states:
// No contracts: <EmptyState icon={FileText} title="No contracts on file" description="Upload your BAA, DPA, or SLA agreement to track compliance." />
// No certifications: <EmptyState icon={Award} title="No certifications" description="Contact sales to begin your SOC2 or HIPAA certification process." />
```
