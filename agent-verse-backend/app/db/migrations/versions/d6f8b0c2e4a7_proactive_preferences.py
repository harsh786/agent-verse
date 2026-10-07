"""proactive_preferences: the stored opt-in record for proactive outreach (a10-F227-02).

The proactive engine was wired with no preferences provider, so every principal
got the permissive default (enabled, no quiet hours) and nothing was stored.
Outreach is now opt-in: a principal is contacted only once a row exists here
(see ``app/proactive/preferences.py``). One row per (tenant, principal):
``enabled``, allowed ``channels``, quiet hours (both NULL = none) measured in
``timezone``, and ``max_per_day`` (enforced by the shared Redis daily cap).
Tenant-scoped, FORCE RLS.

Revision ID: d6f8b0c2e4a7
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d6f8b0c2e4a7"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "proactive_preferences"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            tenant_id     TEXT NOT NULL,
            principal_id  TEXT NOT NULL,
            enabled       BOOLEAN NOT NULL DEFAULT FALSE,
            channels      TEXT[] NOT NULL DEFAULT ARRAY['web']::TEXT[],
            quiet_start   SMALLINT NULL CHECK (quiet_start BETWEEN 0 AND 23),
            quiet_end     SMALLINT NULL CHECK (quiet_end BETWEEN 0 AND 23),
            timezone      TEXT NOT NULL DEFAULT 'UTC',
            max_per_day   INTEGER NOT NULL DEFAULT 3 CHECK (max_per_day BETWEEN 1 AND 20),
            updated_by    TEXT NULL,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, principal_id),
            CHECK ((quiet_start IS NULL) = (quiet_end IS NULL))
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
