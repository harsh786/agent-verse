"""State-machine instances are keyed per machine (TRG-40).

``trigger_state_machine_instances`` was unique on (tenant_id, entity_id), so a
second state machine tracking the same entity (an order's payment machine and
its fulfilment machine) shared — and overwrote — the first machine's instance.
The key becomes (tenant_id, machine_id, entity_id).

Downgrade restores the old index and fails if two machines now track the same
entity (that data cannot be represented under the old key).

Revision ID: d3a7c1e9f2b4
Revises: c8d2f4a6b1e3
"""

from __future__ import annotations

from alembic import op

revision = "d3a7c1e9f2b4"
down_revision = "c8d2f4a6b1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tsm_inst_tenant_machine_entity "
        "ON trigger_state_machine_instances (tenant_id, machine_id, entity_id)"
    )
    op.execute("DROP INDEX IF EXISTS uq_tsm_inst_tenant_entity")


def downgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tsm_inst_tenant_entity "
        "ON trigger_state_machine_instances (tenant_id, entity_id)"
    )
    op.execute("DROP INDEX IF EXISTS uq_tsm_inst_tenant_machine_entity")
