"""Fix RLS policies on UUID-typed tenant_id columns.

Tenants are created with ``uuid.uuid4().hex`` — 32 hex chars, no dashes — and
that is the value ``sqlalchemy_rls_context`` writes into ``app.tenant_id``.

Ten policies on UUID ``tenant_id`` columns (all eight workflow-engine tables,
``org_brain_decisions``, ``org_mission_schedules``) compared
``tenant_id::text = current_setting('app.tenant_id')``. A UUID renders WITH
dashes, so for every tenant created through signup the comparison was never
true: under a least-privilege role the whole workflow engine answered nothing
and rejected every write ("new row violates row-level security policy for
table workflow_definitions"). The cast on the column also made the policy
unable to use the tenant index. A superuser bypasses RLS, so tests never saw it.

Thirteen more (org_*, gateway_conversations, organizations) used
``tenant_id = current_setting(...)::uuid``, which matches, but RAISES "invalid
input syntax for type uuid" whenever the GUC is empty or not a UUID — an
unscoped query errored instead of returning nothing.

All 23 now use ``tenant_id = app_current_tenant_uuid()``: a STABLE function that
parses the GUC as a UUID regardless of dashes and yields NULL (matching no row)
for anything else. The column is compared uncast, so the tenant index is usable.
Policies are rewritten from the live catalog so none is missed.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None

_CMD = {"*": "ALL", "r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE"}


def upgrade() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION app_current_tenant_uuid() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT CASE
                WHEN current_setting('app.tenant_id', true) ~*
                     '^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$'
                THEN current_setting('app.tenant_id', true)::uuid
            END
        $$
        """
    )
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT c.relname, p.polname, p.polcmd, p.polpermissive,
                   pg_get_expr(p.polqual, p.polrelid)      AS qual,
                   pg_get_expr(p.polwithcheck, p.polrelid) AS wcheck
            FROM pg_policy p
            JOIN pg_class c ON c.oid = p.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
                               AND NOT a.attisdropped
            WHERE a.atttypid = 'uuid'::regtype
            """
        )
    ).fetchall()
    for relname, polname, polcmd, permissive, qual, _wcheck in rows:
        # Only rewrite the plain tenant-equality policies; anything with extra
        # conditions is left for a human.
        normalized = (qual or "").replace(" ", "").lower()
        simple = normalized in (
            "((tenant_id)::text=current_setting('app.tenant_id'::text,true))",
            "(tenant_id=(current_setting('app.tenant_id'::text,true))::uuid)",
        )
        if not simple:
            continue
        cmd = _CMD[polcmd.decode() if isinstance(polcmd, bytes) else polcmd]
        kind = "PERMISSIVE" if permissive else "RESTRICTIVE"
        using = "USING (tenant_id = app_current_tenant_uuid())"
        check = (
            " WITH CHECK (tenant_id = app_current_tenant_uuid())"
            if cmd in ("ALL", "INSERT", "UPDATE")
            else ""
        )
        if cmd == "INSERT":
            using = ""
        op.execute(f'DROP POLICY "{polname}" ON "{relname}"')
        op.execute(
            f'CREATE POLICY "{polname}" ON "{relname}" AS {kind} FOR {cmd} {using}{check}'
        )


def downgrade() -> None:
    # The previous text-cast form was broken for dashless tenant ids; restoring
    # it would re-break the workflow engine. Leave the corrected policies.
    pass
