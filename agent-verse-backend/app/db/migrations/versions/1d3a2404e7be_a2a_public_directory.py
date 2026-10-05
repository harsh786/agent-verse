"""D3: public A2A agent directory — tenant switch, per-agent opt-in, card projection.

* ``tenants.a2a_directory_enabled`` (default FALSE): the tenant's switch.
* ``agents.a2a_public`` (default FALSE) + ``a2a_description`` / ``a2a_skills``:
  the per-agent opt-in and the card text the owner writes for the directory.
* ``a2a_public_agents``: one row per opted-in, active agent holding ONLY the
  public card fields (name, description, skills). Written in the same
  transaction as the agent change and removed with it (FK cascade too). Because
  it holds nothing private, its SELECT policy is permissive, so the
  unauthenticated directory reads it with the application role instead of a
  BYPASSRLS connection; every write is tenant-checked. RLS enabled and FORCEd.
  Keyset reads walk the primary key.

Existing agents stay private; nothing is listed until a tenant admin turns the
directory on and an agent opts in.

Revision ID: 1d3a2404e7be
Revises: f2c3d4e5a6b7
"""

from __future__ import annotations

from alembic import op

revision = "1d3a2404e7be"
down_revision = "f2c3d4e5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
        "a2a_directory_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )
    for column in (
        "a2a_public BOOLEAN NOT NULL DEFAULT FALSE",
        "a2a_description TEXT NOT NULL DEFAULT ''",
        "a2a_skills JSON NOT NULL DEFAULT '[]'",
    ):
        op.execute(f"ALTER TABLE agents ADD COLUMN IF NOT EXISTS {column}")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a2a_public_agents (
            agent_id    VARCHAR(32)  PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
            tenant_id   VARCHAR(36)  NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name        VARCHAR(200) NOT NULL,
            description TEXT         NOT NULL DEFAULT '',
            skills      JSONB        NOT NULL DEFAULT '[]'::jsonb,
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_a2a_public_agents_tenant "
        "ON a2a_public_agents (tenant_id, agent_id)"
    )
    op.execute("ALTER TABLE a2a_public_agents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE a2a_public_agents FORCE ROW LEVEL SECURITY")
    for name in ("public_read", "tenant_insert", "tenant_update", "tenant_delete"):
        op.execute(f"DROP POLICY IF EXISTS a2a_public_agents_{name} ON a2a_public_agents")
    tenant = "tenant_id = current_setting('app.tenant_id', TRUE)"
    op.execute(
        "CREATE POLICY a2a_public_agents_public_read ON a2a_public_agents "
        "FOR SELECT USING (TRUE)"
    )
    op.execute(
        "CREATE POLICY a2a_public_agents_tenant_insert ON a2a_public_agents "
        f"FOR INSERT WITH CHECK ({tenant})"
    )
    op.execute(
        "CREATE POLICY a2a_public_agents_tenant_update ON a2a_public_agents "
        f"FOR UPDATE USING ({tenant}) WITH CHECK ({tenant})"
    )
    op.execute(
        "CREATE POLICY a2a_public_agents_tenant_delete ON a2a_public_agents "
        f"FOR DELETE USING ({tenant})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS a2a_public_agents")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS a2a_skills")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS a2a_description")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS a2a_public")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS a2a_directory_enabled")
