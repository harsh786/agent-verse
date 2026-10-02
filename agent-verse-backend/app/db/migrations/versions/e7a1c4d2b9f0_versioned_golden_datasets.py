"""Versioned golden datasets: one row per golden-task revision (MEM-54).

A suite's golden tasks were one JSON array on its ``eval_suites`` row: no task
could be edited or deleted, there was no dataset version, every add rewrote the
whole array, and runs could not say which tasks they ran. ``golden_tasks`` (an
unused second store) becomes the source of truth, copy-on-write:

* ``eval_suites.dataset_version`` is bumped by every add / edit / delete / import;
* a revision row is valid for versions ``[valid_from, valid_to)``; an edit closes
  the current row (``valid_to``) and inserts a new one, a delete only closes it,
  so every past version stays exactly reproducible;
* a trigger makes closed (published) revisions immutable and lets an open one
  change only by being closed;
* ``eval_suite_results.dataset_version`` records the version a run executed.

The JSON column is backfilled into rows (version 1) and dropped.

Revision ID: e7a1c4d2b9f0
Revises: cf87de8eae52
"""

from __future__ import annotations

from alembic import op

revision = "e7a1c4d2b9f0"
down_revision = "cf87de8eae52"
branch_labels = None
depends_on = None

_NEW_ID = "replace(gen_random_uuid()::text, '-', '')"


def upgrade() -> None:
    # FORCE RLS hides every row from the migration role (no tenant GUC).
    op.execute("ALTER TABLE golden_tasks NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_suites NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        ALTER TABLE golden_tasks
            ADD COLUMN IF NOT EXISTS task_id TEXT NOT NULL DEFAULT {_NEW_ID},
            ADD COLUMN IF NOT EXISTS valid_from INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS valid_to INTEGER,
            ADD COLUMN IF NOT EXISTS position BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS expected_phrases JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS expected_output TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS max_iterations INTEGER NOT NULL DEFAULT 15
        """
    )
    # Rows written by the old (unused) helper: their id was the task id.
    op.execute(
        """
        UPDATE golden_tasks
           SET task_id = id,
               expected_phrases = CASE WHEN COALESCE(expected_output_contains, '') <> ''
                                       THEN jsonb_build_array(expected_output_contains)
                                       ELSE '[]'::jsonb END
        """
    )
    op.execute("ALTER TABLE golden_tasks DROP COLUMN IF EXISTS expected_output_contains")
    op.execute(
        "ALTER TABLE eval_suites ADD COLUMN IF NOT EXISTS dataset_version INTEGER "
        "NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE eval_suite_results ADD COLUMN IF NOT EXISTS dataset_version INTEGER"
    )
    op.execute(
        f"""
        INSERT INTO golden_tasks
            (id, eval_suite_id, tenant_id, task_id, goal, expected_phrases,
             expected_tool_calls, forbidden_tools, expected_output, min_score,
             max_iterations, tags, valid_from, position, created_at)
        SELECT {_NEW_ID}, x.suite_id, x.tenant_id,
               x.task_id || CASE WHEN x.rn > 1 THEN '-' || x.rn ELSE '' END,
               x.goal, x.phrases, x.tools, x.forbidden, x.expected_output,
               x.min_score, x.max_iterations, x.tags, 1, x.ord, now()
          FROM (
            SELECT s.id AS suite_id, s.tenant_id, t.ord,
                   COALESCE(NULLIF(t.value->>'task_id', ''), {_NEW_ID}) AS task_id,
                   row_number() OVER (
                       PARTITION BY s.tenant_id, s.id, t.value->>'task_id' ORDER BY t.ord
                   ) AS rn,
                   COALESCE(t.value->>'goal', '') AS goal,
                   COALESCE((t.value->'expected_output_contains')::jsonb, '[]'::jsonb) AS phrases,
                   COALESCE((t.value->'expected_tools')::jsonb, '[]'::jsonb) AS tools,
                   COALESCE((t.value->'forbidden_tools')::jsonb, '[]'::jsonb) AS forbidden,
                   COALESCE(t.value->>'expected_output', '') AS expected_output,
                   COALESCE((t.value->>'min_score')::float, 0.8) AS min_score,
                   COALESCE((t.value->>'max_iterations')::int, 15) AS max_iterations,
                   COALESCE((t.value->'tags')::jsonb, '[]'::jsonb) AS tags
              FROM eval_suites s,
                   json_array_elements(COALESCE(s.tasks, '[]'::json))
                       WITH ORDINALITY AS t(value, ord)
          ) x
        """
    )
    op.execute(
        """
        UPDATE eval_suites s SET dataset_version = 1
         WHERE EXISTS (SELECT 1 FROM golden_tasks g
                        WHERE g.tenant_id = s.tenant_id AND g.eval_suite_id = s.id)
        """
    )
    op.execute("ALTER TABLE eval_suites DROP COLUMN IF EXISTS tasks")
    op.execute("ALTER TABLE eval_suites FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE golden_tasks FORCE ROW LEVEL SECURITY")

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_tasks_current "
        "ON golden_tasks (tenant_id, eval_suite_id, task_id) WHERE valid_to IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_tasks_revision "
        "ON golden_tasks (tenant_id, eval_suite_id, task_id, valid_from)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_golden_tasks_suite_position "
        "ON golden_tasks (tenant_id, eval_suite_id, position, task_id)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION golden_tasks_revision_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.valid_to IS NOT NULL THEN
                RAISE EXCEPTION 'golden task revision % is published and immutable', OLD.id;
            END IF;
            IF (NEW.id, NEW.tenant_id, NEW.eval_suite_id, NEW.task_id, NEW.goal,
                NEW.expected_phrases, NEW.expected_tool_calls, NEW.forbidden_tools,
                NEW.expected_output, NEW.min_score, NEW.max_iterations, NEW.tags,
                NEW.valid_from, NEW.position)
               IS DISTINCT FROM
               (OLD.id, OLD.tenant_id, OLD.eval_suite_id, OLD.task_id, OLD.goal,
                OLD.expected_phrases, OLD.expected_tool_calls, OLD.forbidden_tools,
                OLD.expected_output, OLD.min_score, OLD.max_iterations, OLD.tags,
                OLD.valid_from, OLD.position) THEN
                RAISE EXCEPTION 'golden task revision % may only be closed, not edited', OLD.id;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_golden_tasks_immutable ON golden_tasks")
    op.execute(
        "CREATE TRIGGER trg_golden_tasks_immutable BEFORE UPDATE ON golden_tasks "
        "FOR EACH ROW EXECUTE FUNCTION golden_tasks_revision_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_golden_tasks_immutable ON golden_tasks")
    op.execute("DROP FUNCTION IF EXISTS golden_tasks_revision_immutable()")
    op.execute("ALTER TABLE golden_tasks NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE eval_suites NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE eval_suites ADD COLUMN IF NOT EXISTS tasks JSON NOT NULL DEFAULT '[]'"
    )
    op.execute(
        """
        UPDATE eval_suites s SET tasks = COALESCE((
            SELECT json_agg(json_build_object(
                       'task_id', g.task_id, 'goal', g.goal,
                       'expected_tools', g.expected_tool_calls,
                       'forbidden_tools', g.forbidden_tools,
                       'expected_output_contains', g.expected_phrases,
                       'expected_output', g.expected_output,
                       'min_score', g.min_score, 'max_iterations', g.max_iterations,
                       'tags', g.tags) ORDER BY g.position, g.task_id)
              FROM golden_tasks g
             WHERE g.tenant_id = s.tenant_id AND g.eval_suite_id = s.id
               AND g.valid_to IS NULL), '[]'::json)
        """
    )
    op.execute("DELETE FROM golden_tasks WHERE valid_to IS NOT NULL")
    op.execute("DROP INDEX IF EXISTS ix_golden_tasks_suite_position")
    op.execute("DROP INDEX IF EXISTS uq_golden_tasks_revision")
    op.execute("DROP INDEX IF EXISTS uq_golden_tasks_current")
    op.execute(
        "ALTER TABLE golden_tasks ADD COLUMN IF NOT EXISTS expected_output_contains TEXT "
        "NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE golden_tasks DROP COLUMN IF EXISTS max_iterations, "
        "DROP COLUMN IF EXISTS expected_output, DROP COLUMN IF EXISTS expected_phrases, "
        "DROP COLUMN IF EXISTS position, DROP COLUMN IF EXISTS valid_to, "
        "DROP COLUMN IF EXISTS valid_from, DROP COLUMN IF EXISTS task_id"
    )
    op.execute("ALTER TABLE eval_suite_results DROP COLUMN IF EXISTS dataset_version")
    op.execute("ALTER TABLE eval_suites DROP COLUMN IF EXISTS dataset_version")
    op.execute("ALTER TABLE eval_suites FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE golden_tasks FORCE ROW LEVEL SECURITY")
