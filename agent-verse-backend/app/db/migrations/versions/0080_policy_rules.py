"""Declarative policy rules."""
from alembic import op
import sqlalchemy as sa

revision = '0080'
down_revision = '0079'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'policy_rules',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('tenant_id', sa.String, nullable=False, index=True),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('version', sa.Integer, server_default='1', nullable=False),
        sa.Column('rule_json', sa.JSON, nullable=False),
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE policy_rules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE policy_rules FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON policy_rules USING (tenant_id = current_setting('app.tenant_id', TRUE)) WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))")


def downgrade() -> None:
    op.drop_table('policy_rules')
