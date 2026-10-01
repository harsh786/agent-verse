"""oauth_tokens.needs_reauth: a connection whose stored token cannot be used.

Set when a stored OAuth token cannot be decrypted (the token used to be
returned as-is — the ciphertext went out as a Bearer token); every replica and
the worker then refuse the connection until a new token is stored, which clears
it (OAUTH-DECRYPT-FAILOPEN).

Revision ID: e5f1a3b9c4d0
Revises: cf87de8eae52
"""

from __future__ import annotations

from alembic import op

revision = "e5f1a3b9c4d0"
down_revision = "cf87de8eae52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE oauth_tokens ADD COLUMN IF NOT EXISTS needs_reauth "
        "BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE oauth_tokens DROP COLUMN IF EXISTS needs_reauth")
