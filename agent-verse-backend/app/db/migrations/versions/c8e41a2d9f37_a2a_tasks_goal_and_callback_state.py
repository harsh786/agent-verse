"""a2a_tasks: goal_id and durable callback-delivery state (A2A-01).

The goal used to be submitted by an untracked asyncio task after the 202, so
a restart stranded the task as ``accepted`` with no goal or callback. The API
now submits the goal before answering and stores its ``goal_id`` (status
``working``); a beat reconciler finalises tasks from their goals and callbacks
are claimed/retried from these columns.

Revision ID: c8e41a2d9f37
Revises: f1e790b4050a
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e41a2d9f37"
down_revision: str | None = "f1e790b4050a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("a2a_tasks", sa.Column("goal_id", sa.String(32), nullable=True))
    op.add_column(
        "a2a_tasks",
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "a2a_tasks", sa.Column("callback_next_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "a2a_tasks",
        sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The reconciler scans only open tasks and due callbacks: keep both scans on
    # small partial indexes however large the table grows (callback_next_at is
    # NULL once a callback is delivered, given up, or there is none).
    op.create_index(
        "ix_a2a_tasks_open",
        "a2a_tasks",
        ["created_at"],
        postgresql_where=sa.text("status IN ('accepted', 'working')"),
    )
    op.create_index(
        "ix_a2a_tasks_callback_due",
        "a2a_tasks",
        ["callback_next_at"],
        postgresql_where=sa.text("callback_next_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_a2a_tasks_callback_due", table_name="a2a_tasks")
    op.drop_index("ix_a2a_tasks_open", table_name="a2a_tasks")
    op.drop_column("a2a_tasks", "callback_delivered_at")
    op.drop_column("a2a_tasks", "callback_next_at")
    op.drop_column("a2a_tasks", "callback_attempts")
    op.drop_column("a2a_tasks", "goal_id")
