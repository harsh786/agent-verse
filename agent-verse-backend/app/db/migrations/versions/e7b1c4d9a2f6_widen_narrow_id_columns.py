"""Widen every id-carrying VARCHAR(32) column to VARCHAR(64) (P8b-4)

P4-2 (d4e7a2c9b1f3) widened only the audit/event tables. About 95 other
``tenant_id`` columns (approval_requests, goal_fanout_ledger,
progress_ledger_revisions, ...) and the ``id`` / ``*_id`` columns that carry the
same identifiers stayed ``VARCHAR(32)``: the 32-char hex form tenants are
created with. A dashed UUID (36 chars) — which ``tenants.id`` (``VARCHAR(36)``)
admits and a resumed workflow run carries — overflows them
(``StringDataRightTruncationError``).

The column list below was GENERATED from the live schema of a freshly migrated
Postgres testcontainer (every ``character varying`` column narrower than 36
named ``tenant_id``, ``id`` or ``*_id`` on a table or partitioned parent; all
were 32), not written by hand. Each is widened to ``VARCHAR(64)``; partitions
follow their parent.

Raising a varchar limit is catalog-only in Postgres (no rewrite; indexes and
foreign keys are kept — FK pairs are all widened together). What blocks
``ALTER COLUMN ... TYPE`` is handled the P4-2 way, generalised:

* RLS policies — those on the altered tables (and their partitions) and any
  other policy whose expression references an altered column — are captured
  from ``pg_policies``, dropped, and recreated verbatim afterwards.
* Views reading an altered column (and views on top of those) are captured
  (definition, options, grants), dropped, and recreated afterwards.

Idempotent: a missing table/column or one already at the target width is
skipped. ``downgrade`` narrows back to 32 and fails loudly (never truncates)
when a row already holds a longer value.

Revision ID: e7b1c4d9a2f6
Revises: f6a9d4e2b8c5
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "e7b1c4d9a2f6"
down_revision: str | None = "f6a9d4e2b8c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WIDE = 64

# Generated from a migrated testcontainer (see the module docstring). Every
# column in _COLUMNS was VARCHAR(32) before this revision; the ``tenant_id``
# columns in _TENANT_ID_36 were VARCHAR(36) (widened so that no tenant_id is
# narrower than 64 — the guard in tests/db/test_id_column_width_guard.py).
_COLUMNS: dict[str, tuple[str, ...]] = {
    "a2a_public_agents": ("agent_id",),
    "a2a_tasks": ("goal_id",),
    "ab_test_results": ("goal_id", "id", "tenant_id"),
    "agent_bids": ("id", "tenant_id"),
    "agent_permissions": ("agent_id", "id", "tenant_id"),
    "agent_templates": ("id", "tenant_id"),
    "agents": ("id", "tenant_id"),
    "allocations": ("id", "tenant_id"),
    "approval_grants": ("id", "tenant_id"),
    "approval_requests": ("id", "tenant_id"),
    "approval_votes": ("request_id", "tenant_id"),
    "auction_registry": ("id", "tenant_id"),
    "billing_subscriptions": ("id", "tenant_id"),
    "budget_accounts": ("id", "tenant_id"),
    "budget_entries": ("id", "tenant_id"),
    "budget_reservations": ("id", "tenant_id"),
    "camel_dialogue_state": ("id", "tenant_id"),
    "chat_artifacts": ("id", "message_id", "session_id", "tenant_id"),
    "chat_channel_sessions": ("chat_session_id",),
    "chat_message_usage": ("id", "message_id", "session_id", "tenant_id"),
    "chat_messages": ("branch_id", "goal_id", "id", "parent_message_id", "session_id", "tenant_id"),
    "chat_principal_sessions": ("chat_session_id",),
    "chat_session_folders": ("id", "tenant_id"),
    "chat_sessions": ("agent_id", "folder_id", "id", "tenant_id"),
    "claims": ("id", "tenant_id"),
    "collab_operations": ("id", "session_id", "tenant_id"),
    "collab_sessions": ("id", "tenant_id"),
    "context_messages": ("id", "tenant_id"),
    "coordination_consumptions": ("id", "tenant_id"),
    "coordination_sessions": ("id", "tenant_id"),
    "decision_traces": ("goal_id", "id", "step_id", "tenant_id"),
    "documents": ("collection_id", "id", "tenant_id"),
    "episodic_memories": ("goal_id", "id", "tenant_id"),
    "eval_scorecards": ("goal_id", "id", "tenant_id"),
    "eval_suite_results": ("agent_id", "id", "run_id", "suite_id", "tenant_id"),
    "eval_suite_task_results": ("run_id", "suite_id"),
    "eval_suites": ("id", "tenant_id"),
    "evaluations": ("goal_id", "id", "tenant_id"),
    "execution_memory": ("id", "tenant_id"),
    "experiment_registry": ("agent_id", "id", "tenant_id"),
    "generative_agent_state": ("id", "tenant_id"),
    "goal_checkpoints": ("goal_id", "id", "tenant_id"),
    "goal_events": ("goal_id",),
    "goal_fanout_ledger": ("parent_goal_id", "tenant_id"),
    "goal_steps": ("goal_id", "id", "tenant_id"),
    "goals": ("agent_id", "id", "parent_goal_id", "tenant_id"),
    "golden_dataset_items": ("dataset_id", "id", "tenant_id"),
    "golden_datasets": ("id", "tenant_id"),
    "handoffs": ("id", "tenant_id"),
    "ip_allowlist": ("id", "tenant_id"),
    "knowledge_chunks_1024": ("parent_chunk_id",),
    "knowledge_chunks_1536": ("parent_chunk_id",),
    "knowledge_chunks_2048": ("parent_chunk_id",),
    "knowledge_chunks_768": ("parent_chunk_id",),
    "learning_experiment_outcomes": ("tenant_id",),
    "learning_experiments": ("agent_id", "tenant_id"),
    "long_term_memory": ("id", "source_goal_id", "tenant_id"),
    "memory_backfill_checkpoints": ("tenant_id",),
    "memory_conflicts": ("id", "tenant_id"),
    "memory_feedback": ("id", "memory_id", "tenant_id"),
    "memory_records": ("agent_id", "id", "source_goal_id", "tenant_id"),
    "moa_layers": ("id", "tenant_id"),
    "moa_proposals": ("id", "tenant_id"),
    "oauth_tokens": ("id", "server_id", "tenant_id"),
    "policies": ("id", "tenant_id"),
    "procedural_memories": ("id", "tenant_id"),
    "progress_ledger_revisions": ("id", "tenant_id"),
    "prospective_memories": ("id", "source_goal_id", "tenant_id"),
    "raft_confirmation_grants": ("dataset_id", "tenant_id"),
    "raft_datasets": ("collection_id", "id", "tenant_id"),
    "raft_fine_tune_jobs": ("collection_id", "dataset_id", "id", "tenant_id"),
    "raft_model_deployments": ("collection_id", "job_id", "tenant_id"),
    "reasoning_promotion_decisions": ("baseline_id", "id", "tenant_id"),
    "reflexion_lessons": ("id", "source_goal_id", "tenant_id"),
    "regression_baselines": ("id", "tenant_id"),
    "regression_cases": ("goal_id", "id", "tenant_id"),
    "routing_decisions": ("goal_id", "tenant_id"),
    "routing_outcomes": ("tenant_id",),
    "schedules": ("agent_id", "id", "tenant_id"),
    "self_improvement_actions": ("goal_id", "id", "tenant_id"),
    "semantic_cache_entries": ("id",),
    "skills": ("id", "tenant_id"),
    "solutions": ("eval_suite_id", "id"),
    "strategy_artifacts": ("id", "tenant_id"),
    "strategy_certification_evidence": ("id", "tenant_id"),
    "strategy_checkpoints": ("id", "tenant_id"),
    "strategy_executions": ("id", "tenant_id"),
    "strategy_run_checkpoints": ("goal_id", "tenant_id"),
    "swarm_gossip_messages": ("id", "tenant_id"),
    "task_auctions": ("id", "tenant_id"),
    "tenant_memberships": ("id", "tenant_id", "user_id"),
    "thought_edges": ("id", "tenant_id"),
    "thought_nodes": ("id", "tenant_id"),
    "tool_capabilities": ("connector_id", "id", "tenant_id"),
    "tool_trust_records": ("id", "tenant_id"),
    "training_export_jobs": ("id",),
    "usage_records": ("goal_id", "id", "tenant_id"),
    "user_roles": ("id", "tenant_id"),
    "user_sessions": ("id", "user_id"),
    "users": ("id",),
    "verifier_calibration": ("goal_id", "id", "tenant_id"),
    "work_items": ("id", "tenant_id"),
}
_TENANT_ID_36: tuple[str, ...] = (
    "a2a_public_agents",
    "api_keys",
    "trigger_audit_events",
    "trigger_dlq",
    "trigger_events",
    "user_sessions",
)


def _original_widths() -> dict[tuple[str, str], int]:
    widths = {(t, c): 32 for t, cols in _COLUMNS.items() for c in cols}
    widths.update({(t, "tenant_id"): 36 for t in _TENANT_ID_36})
    return widths


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _targets(bind: Any, plan: dict[tuple[str, str], int]) -> list[tuple[str, str, int]]:
    """(table, column, width) for planned columns that exist at another width."""
    rows = bind.execute(
        sa.text(
            "SELECT table_name, column_name, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ANY(:tables) "
            "AND data_type = 'character varying'"
        ),
        {"tables": sorted({t for t, _ in plan})},
    ).all()
    present = {(r[0], r[1]): r[2] for r in rows}
    return [
        (table, col, width)
        for (table, col), width in sorted(plan.items())
        if (table, col) in present and present[(table, col)] != width
    ]


def _family(bind: Any, tables: list[str]) -> list[str]:
    """*tables* plus every partition below them."""
    rows = bind.execute(
        sa.text(
            "WITH RECURSIVE fam(oid) AS ("
            "  SELECT c.oid FROM pg_class c"
            "  WHERE c.relname = ANY(:t) AND c.relnamespace = current_schema()::regnamespace"
            "  UNION SELECT i.inhrelid FROM pg_inherits i JOIN fam ON i.inhparent = fam.oid"
            ") SELECT c.relname FROM pg_class c JOIN fam ON fam.oid = c.oid"
        ),
        {"t": tables},
    ).scalars()
    return sorted(set(rows))


def _column_refs(targets: list[tuple[str, str, int]]) -> str:
    """SQL VALUES list of (regclass, column name) for the target columns."""
    return ", ".join(
        f"(to_regclass({_literal(_quote(t))}), {_literal(c)})" for t, c, _ in targets
    )


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _policies(
    bind: Any, targets: list[tuple[str, str, int]], family: list[str]
) -> list[dict[str, Any]]:
    """Policies on the altered tables, plus any policy referencing a target column."""
    rows = bind.execute(
        sa.text(
            "WITH cols(rel, attname) AS (VALUES " + _column_refs(targets) + "), "
            "refs AS ("
            "  SELECT DISTINCT pol.oid FROM pg_policy pol"
            "  JOIN pg_depend d ON d.classid = 'pg_policy'::regclass AND d.objid = pol.oid"
            "  JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid"
            "  JOIN cols ON cols.rel = a.attrelid AND cols.attname = a.attname"
            ") "
            "SELECT p.tablename, p.policyname, p.permissive, p.roles, p.cmd, p.qual, "
            "p.with_check FROM pg_policies p "
            "JOIN pg_class c ON c.relname = p.tablename "
            " AND c.relnamespace = current_schema()::regnamespace "
            "JOIN pg_policy pol ON pol.polrelid = c.oid AND pol.polname = p.policyname "
            "WHERE p.schemaname = current_schema() "
            "AND (p.tablename = ANY(:family) OR pol.oid IN (SELECT oid FROM refs))"
        ),
        {"family": family},
    ).mappings()
    return [dict(r) for r in rows]


def _create_policy_sql(p: dict[str, Any]) -> str:
    roles = list(p["roles"] or ["public"])
    to = ", ".join("public" if r == "public" else _quote(r) for r in roles)
    sql = (
        f"CREATE POLICY {_quote(p['policyname'])} ON {_quote(p['tablename'])} "
        f"AS {p['permissive']} FOR {p['cmd']} TO {to}"
    )
    if p["qual"] is not None:
        sql += f" USING ({p['qual']})"
    if p["with_check"] is not None:
        sql += f" WITH CHECK ({p['with_check']})"
    return sql


def _views(bind: Any, targets: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    """Views over a target column, and views over those, in creation order."""
    rows = bind.execute(
        sa.text(
            "WITH RECURSIVE cols(rel, attname) AS (VALUES " + _column_refs(targets) + "), "
            "direct AS ("
            "  SELECT DISTINCT r.ev_class AS oid FROM pg_depend d"
            "  JOIN pg_rewrite r ON r.oid = d.objid"
            "  JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid"
            "  JOIN cols ON cols.rel = a.attrelid AND cols.attname = a.attname"
            "  WHERE d.classid = 'pg_rewrite'::regclass AND r.ev_class <> d.refobjid"
            "), dep(oid, depth) AS ("
            "  SELECT oid, 1 FROM direct"
            "  UNION ALL SELECT r.ev_class, dep.depth + 1 FROM pg_depend d"
            "  JOIN pg_rewrite r ON r.oid = d.objid"
            "  JOIN dep ON d.refobjid = dep.oid"
            "  WHERE d.classid = 'pg_rewrite'::regclass AND r.ev_class <> dep.oid"
            "  AND dep.depth < 16"
            ") "
            "SELECT c.relname, c.relkind, pg_get_viewdef(c.oid) AS definition, "
            "c.reloptions, max(dep.depth) AS depth, "
            "coalesce((SELECT array_agg(format('GRANT %s ON %I TO %s', x.privilege_type, "
            "  c.relname, CASE WHEN x.grantee = 0 THEN 'PUBLIC' "
            "  ELSE quote_ident(pg_get_userbyid(x.grantee)) END)) "
            "  FROM aclexplode(c.relacl) x WHERE x.grantee <> c.relowner), '{}') AS grants "
            "FROM dep JOIN pg_class c ON c.oid = dep.oid "
            "GROUP BY c.oid, c.relname, c.relkind, c.reloptions "
            "ORDER BY max(dep.depth)"
        )
    ).mappings()
    views = [dict(r) for r in rows]
    for v in views:
        kind = v["relkind"].decode() if isinstance(v["relkind"], bytes) else str(v["relkind"])
        if kind != "v":
            raise RuntimeError(
                f"{v['relname']} (relkind {kind}) depends on a column being resized; "
                "only plain views are recreated by this migration"
            )
    return views


def _create_view_sql(v: dict[str, Any]) -> str:
    opts = f" WITH ({', '.join(v['reloptions'])})" if v["reloptions"] else ""
    return f"CREATE VIEW {_quote(v['relname'])}{opts} AS {v['definition']}"


def _resize(plan: dict[tuple[str, str], int]) -> None:
    bind = op.get_bind()
    targets = _targets(bind, plan)
    if not targets:
        return
    tables = sorted({t for t, _, _ in targets})
    family = _family(bind, tables)
    policies = _policies(bind, targets, family)
    views = _views(bind, targets)

    for v in reversed(views):  # dependants first
        op.execute(f"DROP VIEW {_quote(v['relname'])}")
    for p in policies:
        op.execute(f"DROP POLICY {_quote(p['policyname'])} ON {_quote(p['tablename'])}")
    for table in tables:
        cols = [(c, w) for t, c, w in targets if t == table]
        # One statement per table; ALTER on a partitioned parent recurses.
        op.execute(
            f"ALTER TABLE {_quote(table)} "
            + ", ".join(f"ALTER COLUMN {_quote(c)} TYPE VARCHAR({w})" for c, w in cols)
        )
    for p in policies:
        op.execute(_create_policy_sql(p))
    for v in views:
        op.execute(_create_view_sql(v))
        for grant in v["grants"] or []:
            op.execute(grant)


def upgrade() -> None:
    _resize(dict.fromkeys(_original_widths(), _WIDE))


def downgrade() -> None:
    # Back to each column's original width. Fails loudly
    # (StringDataRightTruncationError) when a row already holds a longer value —
    # never truncates.
    _resize(_original_widths())
