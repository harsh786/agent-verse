"""add self_optimization_suggestions table

Revision ID: 170245f26dcb
Revises: 0070
Create Date: 2026-07-04 15:21:48.218418
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '170245f26dcb'
down_revision: str | None = '0070'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'self_optimization_suggestions',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('tenant_id', sa.String, nullable=False),
        sa.Column('suggestion_id', sa.String, nullable=False),
        sa.Column('category', sa.String, nullable=False),
        sa.Column('change_type', sa.String, nullable=False, server_default=''),
        sa.Column('description', sa.Text, nullable=False, server_default=''),
        sa.Column('before_text', sa.Text, nullable=False, server_default=''),
        sa.Column('after_text', sa.Text, nullable=False, server_default=''),
        sa.Column('confidence', sa.Float, nullable=False, server_default='0.0'),
        sa.Column('applied', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('rejected', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('source_goal_id', sa.String, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_self_opt_suggestions_tenant',
        'self_optimization_suggestions',
        ['tenant_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_self_opt_suggestions_tenant',
                  table_name='self_optimization_suggestions')
    op.drop_table('self_optimization_suggestions')
