"""Durable connector registry and connector secrets (MCPREG-01 / SECRET-01).

Connector configs (``mcp:servers:*``) and their encrypted credentials
(``mcp:connector_secrets:*``) lived only in Redis: a FLUSHALL or an eviction
lost every tenant's connectors and credentials. Postgres becomes the source of
truth; Redis is only a short-TTL read cache.

``mcp_servers`` / ``mcp_credentials`` were created by 0006 but never written by
application code (the registry was Redis-only), and their shape cannot hold the
data: ``id`` was a global VARCHAR(32) primary key while connection ids are
per-tenant and up to 255 chars ("builtin-github:work-org"), and credentials
were one opaque blob per server instead of one row per secret reference. Both
are rebuilt. A non-empty table is refused (never silently dropped).

* ``mcp_servers`` — PK (tenant_id, id); the full ``MCPServerConfig`` in
  ``config`` (JSONB) plus the columns queries need; ``name_key`` (casefolded,
  whitespace-normalised display name) is UNIQUE per tenant, which makes
  connector creation atomic (two concurrent same-name creates: one 409).
* ``mcp_credentials`` — PK (tenant_id, server_id, secret_key); the value is
  vault ciphertext (the tenant's envelope key when it has one, ``tv1:``).
* ``mcp_builtin_provisioning`` — the per-tenant "built-ins provisioned" marker
  (was a Redis key: after a flush every built-in the tenant removed came back).
* ``connector_store_backfills`` — records the one-time Redis → Postgres copy
  (``agentverse connectors-backfill``, also run automatically on startup).

All tenant tables: RLS enabled + forced, USING and WITH CHECK on app.tenant_id.

Revision ID: a7c4e2f9d1b3
Revises: 7cd9f383c99a
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "a7c4e2f9d1b3"
down_revision = "7cd9f383c99a"
branch_labels = None
depends_on = None

_TENANT_TABLES = ("mcp_servers", "mcp_credentials", "mcp_builtin_provisioning")


def _refuse_if_not_empty(table: str) -> None:
    bind = op.get_bind()
    exists = bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar()
    if not exists:
        return
    # The schema owner bypasses RLS on its own tables only when it is not
    # FORCEd: lift FORCE first so a policy cannot hide rows from this check.
    bind.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    has_rows = bind.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar()
    if has_rows:
        raise RuntimeError(
            f"{table} is not empty; it was never written by the application, so "
            "refusing to rebuild it. Inspect and empty it manually, then re-run."
        )


def upgrade() -> None:
    for table in ("mcp_credentials", "mcp_servers"):
        _refuse_if_not_empty(table)
    op.execute("DROP TABLE IF EXISTS mcp_credentials CASCADE")
    op.execute("DROP TABLE IF EXISTS mcp_servers CASCADE")

    op.execute(
        """
        CREATE TABLE mcp_servers (
            tenant_id     VARCHAR(64)  NOT NULL,
            id            VARCHAR(255) NOT NULL,
            name          VARCHAR(200) NOT NULL,
            name_key      VARCHAR(200) NOT NULL,
            url           TEXT         NOT NULL DEFAULT '',
            auth_type     VARCHAR(50)  NOT NULL DEFAULT 'none',
            description   TEXT                  DEFAULT '',
            priority      INTEGER               DEFAULT 0,
            status        VARCHAR(20)           DEFAULT 'active',
            enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
            builtin_type  VARCHAR(255) NOT NULL DEFAULT '',
            config        JSONB        NOT NULL,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_mcp_servers_tenant_name_key ON mcp_servers (tenant_id, name_key)"
    )

    op.execute(
        """
        CREATE TABLE mcp_credentials (
            tenant_id        VARCHAR(64)  NOT NULL,
            server_id        VARCHAR(255) NOT NULL,
            secret_key       VARCHAR(255) NOT NULL,
            encrypted_value  TEXT         NOT NULL,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, server_id, secret_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_builtin_provisioning (
            tenant_id    VARCHAR(64) PRIMARY KEY,
            fingerprint  VARCHAR(128) NOT NULL,
            builtin_ids  JSONB        NOT NULL DEFAULT '[]'::jsonb,
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )

    # Operational record, no tenant data: no RLS.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_store_backfills (
            name          VARCHAR(100) PRIMARY KEY,
            completed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            report        JSONB        NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_store_backfills")
    op.execute("DROP TABLE IF EXISTS mcp_builtin_provisioning")
    op.execute("DROP TABLE IF EXISTS mcp_credentials")
    op.execute("DROP TABLE IF EXISTS mcp_servers")
