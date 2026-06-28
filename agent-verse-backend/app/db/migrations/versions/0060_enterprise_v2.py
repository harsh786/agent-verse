"""enterprise_contracts, saml_configs, scim_configs, compliance_certifications,
whitelabel_configs, scim_tokens

Revision ID: 0060
Revises: 0059
Create Date: 2026-06-28
"""
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Table: enterprise_contracts                                          #
    # Tracks signed agreements (BAA, DPA, MSA, etc.)                     #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS enterprise_contracts (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            contract_type   TEXT NOT NULL
                            CHECK (contract_type IN ('baa','dpa','msa','nda','sla','custom')),
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','signed','expired','terminated')),
            version         TEXT NOT NULL DEFAULT '1.0',
            signed_by_name  TEXT,
            signed_by_email TEXT,
            signed_at       TIMESTAMPTZ,
            expires_at      TIMESTAMPTZ,
            document_url    TEXT,
            document_hash   TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_enterprise_contracts_tenant
            ON enterprise_contracts(tenant_id, contract_type)
            WHERE status = 'signed'
    """)

    # ------------------------------------------------------------------ #
    # Table: saml_configs                                                  #
    # Per-tenant SAML 2.0 IdP configuration                              #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS saml_configs (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
            idp_entity_id   TEXT NOT NULL,
            idp_sso_url     TEXT NOT NULL,
            idp_cert        TEXT NOT NULL,
            sp_entity_id    TEXT NOT NULL,
            attribute_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
            default_role    TEXT NOT NULL DEFAULT 'viewer',
            jit_provisioning BOOLEAN NOT NULL DEFAULT TRUE,
            force_authn     BOOLEAN NOT NULL DEFAULT FALSE,
            name_id_format  TEXT NOT NULL DEFAULT
                'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ------------------------------------------------------------------ #
    # Table: scim_configs                                                  #
    # SCIM 2.0 provisioning configuration                                 #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS scim_configs (
            id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id           TEXT NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
            bearer_token_hash   TEXT NOT NULL,
            bearer_token_prefix TEXT NOT NULL,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            allow_user_create   BOOLEAN NOT NULL DEFAULT TRUE,
            allow_user_update   BOOLEAN NOT NULL DEFAULT TRUE,
            allow_user_delete   BOOLEAN NOT NULL DEFAULT FALSE,
            allow_group_sync    BOOLEAN NOT NULL DEFAULT TRUE,
            default_role        TEXT NOT NULL DEFAULT 'viewer',
            group_role_map      JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_sync_at        TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ------------------------------------------------------------------ #
    # Table: compliance_certifications (Amendment 8.1)                    #
    # Per-tenant compliance certification status                          #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS compliance_certifications (
            id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            certification_type  TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'not_certified',
            issued_at           TIMESTAMPTZ,
            expires_at          TIMESTAMPTZ,
            certificate_url     TEXT,
            certified_by        TEXT,
            scope_description   TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_cert_tenant_type UNIQUE (tenant_id, certification_type)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_compliance_certs_tenant
            ON compliance_certifications(tenant_id)
    """)

    # ------------------------------------------------------------------ #
    # Table: whitelabel_configs (Amendment 8.1)                           #
    # Per-tenant white-label branding                                     #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS whitelabel_configs (
            tenant_id           TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            brand_name          TEXT NOT NULL DEFAULT 'AgentVerse',
            logo_url            TEXT,
            primary_color       TEXT NOT NULL DEFAULT '#3B82F6',
            custom_domain       TEXT UNIQUE,
            custom_email_from   TEXT,
            hide_branding       BOOLEAN NOT NULL DEFAULT FALSE,
            terms_url           TEXT,
            privacy_url         TEXT,
            support_email       TEXT,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ------------------------------------------------------------------ #
    # Table: scim_tokens (Amendment 8.2)                                  #
    # Pre-provisioned bearer tokens for SCIM authentication               #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS scim_tokens (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            token_hash  TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at  TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_scim_tokens_hash
            ON scim_tokens(token_hash)
            WHERE revoked_at IS NULL
    """)


def downgrade() -> None:
    for t in [
        "scim_tokens", "whitelabel_configs", "compliance_certifications",
        "scim_configs", "saml_configs", "enterprise_contracts",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
