"""Marketplace monetization tables."""
from alembic import op
import sqlalchemy as sa

revision = '0079_marketplace_monetization'
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'marketplace_author_accounts',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('tenant_id', sa.String, nullable=False, index=True),
        sa.Column('stripe_account_id', sa.String, nullable=True),
        sa.Column('payout_email', sa.String, nullable=True),
        sa.Column('onboarding_complete', sa.Boolean, server_default='false'),
        sa.Column('total_earned_usd', sa.Numeric(10, 4), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column('marketplace_templates', sa.Column('price_usd', sa.Numeric(8, 2), nullable=True))
    op.add_column('marketplace_templates', sa.Column('author_tenant_id', sa.String, nullable=True))
    op.add_column('marketplace_templates', sa.Column('revenue_share_pct', sa.SmallInteger, server_default='70'))
    op.create_table(
        'marketplace_purchases',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('template_id', sa.String, nullable=False, index=True),
        sa.Column('buyer_tenant_id', sa.String, nullable=False),
        sa.Column('amount_usd', sa.Numeric(8, 2), nullable=False),
        sa.Column('stripe_payment_intent', sa.String, nullable=True),
        sa.Column('status', sa.String, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('marketplace_purchases')
    op.drop_column('marketplace_templates', 'revenue_share_pct')
    op.drop_column('marketplace_templates', 'author_tenant_id')
    op.drop_column('marketplace_templates', 'price_usd')
    op.drop_table('marketplace_author_accounts')
