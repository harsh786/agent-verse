"""strategy_checkpoints: pattern discriminator for coordination read models (ORG-25)

The CAMEL, generative-agent, swarm and auction read models (and the new Magentic
and MoA run records) all persist their checkpointed state in ``strategy_checkpoints``,
but the table had no pattern column: every pattern's ``GET`` listed every other
pattern's executions in the same session. ``pattern`` scopes each read model;
existing rows (written by nothing so far) default to ``''``.

0097 also gave the table the generic per-session ``UNIQUE (tenant_id, session_id,
sequence)``, but ``sequence`` here is the version of ONE execution's checkpoint, so
a second execution in a session (version 1 again) was rejected. Checkpoint versions
are now unique per (tenant, pattern, session, execution).

RLS on ``strategy_checkpoints`` is unchanged (forced tenant isolation since 0097).

Revision ID: e7a3c91d5b20
Revises: d4e9a1c7b3f2
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3c91d5b20"
down_revision: str | None = "d4e9a1c7b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_checkpoints",
        sa.Column("pattern", sa.Text(), nullable=False, server_default=""),
    )
    op.drop_constraint(
        "uq_strategy_checkpoints_tenant_session_sequence",
        "strategy_checkpoints",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_strategy_checkpoints_execution_sequence",
        "strategy_checkpoints",
        ["tenant_id", "session_id", "pattern", "execution_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_strategy_checkpoints_execution_sequence", "strategy_checkpoints", type_="unique"
    )
    # Fails if a session holds more than one execution's checkpoints (the reason
    # this revision exists); delete those rows first to downgrade.
    op.create_unique_constraint(
        "uq_strategy_checkpoints_tenant_session_sequence",
        "strategy_checkpoints",
        ["tenant_id", "session_id", "sequence"],
    )
    op.drop_column("strategy_checkpoints", "pattern")
