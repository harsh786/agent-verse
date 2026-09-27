"""api_keys: let authentication find a key by its hash without bypassing RLS.

API-key resolution runs before any tenant context exists — it is what
establishes one — so it cannot be scoped by ``app.tenant_id``. It used to switch
row security off for the lookup (``SET LOCAL row_security = off``), which a
least-privilege NOBYPASSRLS role is not permitted to do. Run as the role the
codebase describes as the production posture, every authenticated request
failed with "query would be affected by row-level security policy for table
api_keys" and answered 401. (Under the superuser the test tier used, RLS was
simply bypassed, so it never showed.)

This adds a second, SELECT-only, permissive policy: a row is visible when the
caller presents its hash in ``app.api_key_hash``. Permissive policies OR
together for SELECT, so tenant-scoped reads are unchanged; INSERT/UPDATE/DELETE
still require ``app.tenant_id`` via the existing policy. Knowing a key's SHA-256
is equivalent to holding the key, so the policy reveals nothing the caller does
not already possess — and an empty/unset GUC matches nothing.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from __future__ import annotations

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS api_keys_by_presented_hash ON api_keys")
    op.execute(
        """
        CREATE POLICY api_keys_by_presented_hash ON api_keys
            AS PERMISSIVE FOR SELECT
            USING (
                COALESCE(current_setting('app.api_key_hash', true), '') <> ''
                AND key_hash = current_setting('app.api_key_hash', true)
            )
        """
    )
    op.execute("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS api_keys_by_presented_hash ON api_keys")
