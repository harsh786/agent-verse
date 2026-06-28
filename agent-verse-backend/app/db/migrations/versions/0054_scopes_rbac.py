"""custom_roles, role_assignments, api_key_scopes, scope_definitions,
ip_allowlist_entries, scope_grants — Agent Scopes/Auth/AuthZ

Revision ID: 0049
Revises: 0048
Create Date: 2026-06-28

Summary of changes:
  - custom_roles:         Tenant-specific + builtin role definitions with
                          JSONB permission sets and parent-role inheritance.
  - role_assignments:     Maps users / API keys to custom or system roles
                          with optional resource scoping, expiry, and revocation.
  - api_key_scopes:       Normalized scope grants per API key (replaces JSONB array).
  - ip_allowlist_entries: Per-tenant CIDR allowlist — first table that is actually
                          enforced at the middleware level.
  - scope_definitions:    Canonical catalog of all platform scopes with risk levels.
  - scope_grants:         Explicit scope delegation between principals.
  - api_keys alterations: Add default_role (default 'viewer'), rotated_from,
                          use_count; downgrade existing admin defaults to viewer.

Downgrade:
  Restores api_keys.scopes JSONB column by aggregating api_key_scopes rows,
  then drops all new tables.
"""
from __future__ import annotations

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # custom_roles
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_roles (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT,
            name            TEXT NOT NULL,
            display_name    TEXT NOT NULL,
            description     TEXT,
            parent_role_id  TEXT REFERENCES custom_roles(id) ON DELETE SET NULL,
            system_role     TEXT,
            permissions     JSONB NOT NULL DEFAULT '[]'::jsonb,
            conditions      JSONB NOT NULL DEFAULT '{}'::jsonb,
            domain          TEXT,
            is_template     BOOLEAN NOT NULL DEFAULT FALSE,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_by      TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ,
            CONSTRAINT uq_custom_role_id_unique UNIQUE (id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_roles_tenant "
        "ON custom_roles(tenant_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_roles_domain "
        "ON custom_roles(domain) WHERE is_template = TRUE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_custom_roles_parent "
        "ON custom_roles(parent_role_id) WHERE parent_role_id IS NOT NULL"
    )
    op.execute("ALTER TABLE custom_roles ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = 'custom_roles' AND policyname = 'custom_roles_isolation'
          ) THEN
            CREATE POLICY custom_roles_isolation ON custom_roles
              USING (
                tenant_id = current_setting('app.tenant_id', TRUE)
                OR is_template = TRUE
              );
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # role_assignments
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_assignments (
            id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id     TEXT NOT NULL,
            user_id       TEXT NOT NULL,
            role_id       TEXT REFERENCES custom_roles(id) ON DELETE CASCADE,
            system_role   TEXT,
            resource_type TEXT,
            resource_id   TEXT,
            conditions    JSONB DEFAULT '{}'::jsonb,
            granted_by    TEXT,
            granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ,
            revoked_at    TIMESTAMPTZ,
            revoked_by    TEXT,
            revoke_reason TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_role_assignments_user "
        "ON role_assignments(user_id, tenant_id) "
        "WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_role_assignments_resource "
        "ON role_assignments(resource_type, resource_id) "
        "WHERE revoked_at IS NULL"
    )
    op.execute("ALTER TABLE role_assignments ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = 'role_assignments'
              AND policyname = 'role_assignments_isolation'
          ) THEN
            CREATE POLICY role_assignments_isolation ON role_assignments
              USING (tenant_id = current_setting('app.tenant_id', TRUE));
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # api_key_scopes
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_key_scopes (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            api_key_id  TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
            tenant_id   TEXT NOT NULL,
            scope       TEXT NOT NULL,
            resource_id TEXT,
            granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at  TIMESTAMPTZ,
            CONSTRAINT uq_api_key_scope UNIQUE (api_key_id, scope, resource_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_key_scopes_key "
        "ON api_key_scopes(api_key_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_key_scopes_scope "
        "ON api_key_scopes(scope)"
    )
    op.execute("ALTER TABLE api_key_scopes ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = 'api_key_scopes'
              AND policyname = 'api_key_scopes_isolation'
          ) THEN
            CREATE POLICY api_key_scopes_isolation ON api_key_scopes
              USING (tenant_id = current_setting('app.tenant_id', TRUE));
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # ip_allowlist_entries  (distinct from legacy ip_allowlist table)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ip_allowlist_entries (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id   TEXT NOT NULL,
            cidr        TEXT NOT NULL,
            label       TEXT,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_by  TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at  TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ip_allowlist_entries_tenant "
        "ON ip_allowlist_entries(tenant_id) WHERE is_active = TRUE"
    )
    op.execute("ALTER TABLE ip_allowlist_entries ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE tablename = 'ip_allowlist_entries'
              AND policyname = 'ip_allowlist_entries_isolation'
          ) THEN
            CREATE POLICY ip_allowlist_entries_isolation ON ip_allowlist_entries
              USING (tenant_id = current_setting('app.tenant_id', TRUE));
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # scope_definitions  (canonical scope catalog)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scope_definitions (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            scope       TEXT NOT NULL UNIQUE,
            resource    TEXT NOT NULL,
            action      TEXT NOT NULL,
            description TEXT NOT NULL,
            risk_level  TEXT NOT NULL DEFAULT 'low'
                        CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
            domain      TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ------------------------------------------------------------------
    # scope_grants  (explicit scope delegation between principals)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scope_grants (
            id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id        TEXT NOT NULL,
            grantor_id       TEXT NOT NULL,
            grantee_id       TEXT NOT NULL,
            scope            TEXT NOT NULL,
            resource_pattern TEXT,
            expires_at       TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scope_grants_tenant "
        "ON scope_grants(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scope_grants_grantee "
        "ON scope_grants(grantee_id)"
    )

    # ------------------------------------------------------------------
    # api_keys alterations
    # ------------------------------------------------------------------
    # Change default role from admin to viewer (least-privilege default)
    op.execute(
        "ALTER TABLE api_keys "
        "ALTER COLUMN scopes SET DEFAULT '{}'::text[]"
    )
    op.execute(
        "ALTER TABLE api_keys "
        "ADD COLUMN IF NOT EXISTS default_role TEXT NOT NULL DEFAULT 'viewer'"
    )
    op.execute(
        "ALTER TABLE api_keys "
        "ADD COLUMN IF NOT EXISTS rotated_from TEXT REFERENCES api_keys(id)"
    )
    op.execute(
        "ALTER TABLE api_keys "
        "ADD COLUMN IF NOT EXISTS use_count BIGINT NOT NULL DEFAULT 0"
    )
    # last_used_at already exists in the ApiKey model (tenant.py), add IF NOT EXISTS
    op.execute(
        "ALTER TABLE api_keys "
        "ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ"
    )
    # Downgrade any over-privileged defaults
    op.execute(
        "UPDATE api_keys SET default_role = 'viewer' WHERE default_role = 'admin'"
    )


def downgrade() -> None:
    # Restore api_keys.scopes JSONB column populated from api_key_scopes BEFORE drop
    op.execute(
        "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scopes_restored JSONB DEFAULT '[]'"
    )
    op.execute(
        """
        UPDATE api_keys ak
        SET scopes_restored = (
            SELECT COALESCE(jsonb_agg(scope), '[]'::jsonb)
            FROM api_key_scopes aks
            WHERE aks.api_key_id = ak.id
        )
        """
    )

    # Drop new tables in reverse dependency order
    op.execute("DROP TABLE IF EXISTS scope_grants CASCADE")
    op.execute("DROP TABLE IF EXISTS scope_definitions CASCADE")
    op.execute("DROP TABLE IF EXISTS ip_allowlist_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS api_key_scopes CASCADE")
    op.execute("DROP TABLE IF EXISTS role_assignments CASCADE")
    op.execute("DROP TABLE IF EXISTS custom_roles CASCADE")

    # Remove columns added to api_keys
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS use_count")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS rotated_from")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS default_role")
