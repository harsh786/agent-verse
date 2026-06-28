"""Merge migration — consolidate parallel migration chains 0039 and 0043.

Chain A: ...→ 0035 → 0037 → 0038 → 0039
Chain B: ...→ 0035 → 0036 → 0040 → 0041 → 0042 → 0043

Both chains are now merged at 0044.
"""

from alembic import op

revision = "0044"
down_revision = ("0039", "0043")  # Merge of both heads
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nothing to do — just merging the two chains
    pass


def downgrade() -> None:
    pass
