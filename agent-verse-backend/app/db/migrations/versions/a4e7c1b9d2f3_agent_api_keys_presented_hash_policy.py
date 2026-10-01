"""agent_api_keys: let authentication find an agent key by its hash (AGKEY-01).

Agent-scoped keys (``av_agent_*``) are resolved before any tenant context exists
— resolving them is what establishes it — so the lookup cannot be scoped by
``app.tenant_id`` and a least-privilege NOBYPASSRLS role sees no row under the
tenant-isolation policy alone. Same answer as ``api_keys_by_presented_hash``
(b8c9d0e1f2a3): a second, SELECT-only permissive policy makes exactly the row
whose hash the caller presents in ``app.agent_key_hash`` visible. Knowing a
key's SHA-256 is equivalent to holding the key; an empty GUC matches nothing.

Revision ID: a4e7c1b9d2f3
Revises: c3d9e1f7a2b8
"""

from __future__ import annotations

from alembic import op

revision = "a4e7c1b9d2f3"
down_revision = "c3d9e1f7a2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_api_keys_by_presented_hash ON agent_api_keys")
    op.execute(
        """
        CREATE POLICY agent_api_keys_by_presented_hash ON agent_api_keys
            AS PERMISSIVE FOR SELECT
            USING (
                COALESCE(current_setting('app.agent_key_hash', true), '') <> ''
                AND key_hash = current_setting('app.agent_key_hash', true)
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_api_keys_by_presented_hash ON agent_api_keys")
