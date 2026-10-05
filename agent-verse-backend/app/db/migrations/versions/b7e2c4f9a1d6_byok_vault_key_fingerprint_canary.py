"""BYOK-2: vault key fingerprint on tenant LLM configs + the vault key canary.

``tenant_llm_configs.vault_key_fingerprint`` records WHICH platform vault key
sealed the tenant's BYOK API key (a non-reversible 16-hex identifier, never the
key). A worker whose VAULT_MASTER_KEY differs from the API's now reports
"vault key mismatch: encrypted with <fp> but this worker has <fp>" instead of
an opaque "could not be decrypted". Nullable: rows saved earlier have none.

``vault_key_canary`` (one row, platform-wide, no tenant data — so no tenant_id
and no RLS) holds a known plaintext encrypted by the API's vault key plus that
key's fingerprint. Workers open it at startup and refuse to start (outside
development) when they cannot; the API's /health/ready re-verifies it.

Revision ID: b7e2c4f9a1d6
Revises: d9f3b6a1c8e4
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7e2c4f9a1d6"
down_revision: str | None = "d9f3b6a1c8e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenant_llm_configs ADD COLUMN IF NOT EXISTS vault_key_fingerprint TEXT")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vault_key_canary (
            id          TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            ciphertext  TEXT NOT NULL,
            written_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vault_key_canary")
    op.execute("ALTER TABLE tenant_llm_configs DROP COLUMN IF EXISTS vault_key_fingerprint")
