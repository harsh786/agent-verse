"""golden_tasks: expected_citations + source_goal_id (a10-F235-01)

A completed goal can now be promoted into an eval suite as a golden task
(``POST /intelligence/eval-suites/{id}/tasks/from-goal/{goal_id}``). Its
expectation is the goal's verified answer, the tools it called and the sources
it cited, so a revision gains:

* ``expected_citations`` — sources a run must cite (a new scored check);
* ``source_goal_id`` — the goal the task was promoted from (provenance).

The immutability trigger now covers both columns, so a published revision can
still only be closed, never edited.

Revision ID: e6c8a0b2d4f7
Revises: d5b7f9a1c3e5
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e6c8a0b2d4f7"
down_revision: str | Sequence[str] | None = "d5b7f9a1c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _immutable_fn(extra: str) -> str:
    new = (
        "NEW.id, NEW.tenant_id, NEW.eval_suite_id, NEW.task_id, NEW.goal, "
        "NEW.expected_phrases, NEW.expected_tool_calls, NEW.forbidden_tools, "
        "NEW.expected_output, NEW.min_score, NEW.max_iterations, NEW.tags, "
        "NEW.valid_from, NEW.position" + extra.replace("X.", "NEW.")
    )
    old = new.replace("NEW.", "OLD.")
    return f"""
        CREATE OR REPLACE FUNCTION golden_tasks_revision_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.valid_to IS NOT NULL THEN
                RAISE EXCEPTION 'golden task revision % is published and immutable', OLD.id;
            END IF;
            IF ({new}) IS DISTINCT FROM ({old}) THEN
                RAISE EXCEPTION 'golden task revision % may only be closed, not edited', OLD.id;
            END IF;
            RETURN NEW;
        END $$
    """


def upgrade() -> None:
    op.execute(
        "ALTER TABLE golden_tasks "
        "ADD COLUMN IF NOT EXISTS expected_citations JSONB NOT NULL DEFAULT '[]'::jsonb, "
        "ADD COLUMN IF NOT EXISTS source_goal_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_golden_tasks_source_goal "
        "ON golden_tasks (tenant_id, source_goal_id) WHERE source_goal_id IS NOT NULL"
    )
    op.execute(_immutable_fn(", X.expected_citations, X.source_goal_id"))


def downgrade() -> None:
    op.execute(_immutable_fn(""))
    op.execute("DROP INDEX IF EXISTS ix_golden_tasks_source_goal")
    op.execute(
        "ALTER TABLE golden_tasks DROP COLUMN IF EXISTS source_goal_id, "
        "DROP COLUMN IF EXISTS expected_citations"
    )
