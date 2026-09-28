"""Enforce RLS on 37 tenant tables that had none, FORCE-less or USING-only policies.

The live-catalog audit (tests/e2e_full/test_security_sweep_e2e.py) listed tenant
tables that a NOBYPASSRLS application role could read/write across tenants, or
whose policies the table owner bypassed (ENABLE without FORCE). Their access
paths were moved onto the tenant RLS context (request paths) or the
maintenance role (cross-tenant system work) first; this revision turns the
database guard on:

* chat/billing: chat_artifacts, chat_message_usage, chat_session_folders,
  cost_ledger, budget_configs, ab_test_results, benchmark_runs (+ read-only
  'global' rows), audit_wal_queue — plus chat_sessions/chat_messages and the
  chat_session_usage_summary view (now security_invoker, so it no longer runs
  with its owner's rights over chat_message_usage);
* identity: saml_configs, scim_configs, scim_tokens (+ SELECT-only
  presented-hash policy for pre-auth SCIM token resolution, like api_keys),
  tenant_mfa, tenant_settings, whitelabel_configs, scope_grants,
  vault_key_versions (platform-only: deny-all, maintenance role only);
* compliance: compliance_certifications (tenant READ only — no
  self-certification), consent_records, deleted_tenants (request / read /
  complete, never rewrite), enterprise_contracts, gdpr_export_jobs,
  golden_tasks, marketplace_author_accounts, org_blueprints (NULL tenant =
  global: readable by all, writable by owner only);
* memory/intelligence: episodic_memories, procedural_memories,
  memory_conflicts, reflexion_lessons, tool_reliability_memory,
  tool_trust_records, agent_optimization_history, improvement_experiments,
  improvement_results, self_improvement_actions, agent_connector_credentials,
  debate_sessions, debate_proposals.

Partitions: Postgres applies a partitioned parent's policies only to access
through the parent. Every partition of the RLS-protected partitioned tables
(cost_ledger, audit_events, policy_evaluations, guardrail_violations) gets
the parent's RLS copied onto it, and ``app_apply_parent_rls(child)`` does the
same for partitions created later by ensure_future_partitions.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
"""

# ruff: noqa: E501  (SQL statements are kept on one line each)
from __future__ import annotations

import re

from alembic import op
from sqlalchemy import text

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

_STATEMENTS = [
    "ALTER TABLE chat_artifacts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE chat_artifacts FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS chat_artifacts_tenant_isolation ON chat_artifacts",
    "CREATE POLICY chat_artifacts_tenant_isolation ON chat_artifacts USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE chat_message_usage ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE chat_message_usage FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS chat_message_usage_tenant_isolation ON chat_message_usage",
    "CREATE POLICY chat_message_usage_tenant_isolation ON chat_message_usage USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE chat_session_folders ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE chat_session_folders FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS chat_session_folders_tenant_isolation ON chat_session_folders",
    "CREATE POLICY chat_session_folders_tenant_isolation ON chat_session_folders USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE cost_ledger ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cost_ledger FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS cost_ledger_isolation ON cost_ledger",
    "CREATE POLICY cost_ledger_isolation ON cost_ledger USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE budget_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE budget_configs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS budget_configs_tenant_isolation ON budget_configs",
    "CREATE POLICY budget_configs_tenant_isolation ON budget_configs USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE ab_test_results ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ab_test_results FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS ab_test_results_tenant_isolation ON ab_test_results",
    "CREATE POLICY ab_test_results_tenant_isolation ON ab_test_results USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE benchmark_runs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE benchmark_runs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS benchmark_runs_tenant_isolation ON benchmark_runs",
    "CREATE POLICY benchmark_runs_tenant_isolation ON benchmark_runs USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "DROP POLICY IF EXISTS benchmark_runs_global_read ON benchmark_runs",
    "CREATE POLICY benchmark_runs_global_read ON benchmark_runs FOR SELECT USING (tenant_id = 'global')",
    "CREATE INDEX IF NOT EXISTS ix_benchmark_runs_tenant_suite ON benchmark_runs (tenant_id, suite_name, created_at DESC)",
    "ALTER TABLE audit_wal_queue ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_wal_queue FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS audit_wal_queue_tenant_isolation ON audit_wal_queue",
    "CREATE POLICY audit_wal_queue_tenant_isolation ON audit_wal_queue USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE saml_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE saml_configs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS saml_configs_isolation ON saml_configs",
    "CREATE POLICY saml_configs_isolation ON saml_configs AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE scim_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE scim_configs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS scim_configs_isolation ON scim_configs",
    "CREATE POLICY scim_configs_isolation ON scim_configs AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE scim_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE scim_tokens FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS scim_tokens_isolation ON scim_tokens",
    "CREATE POLICY scim_tokens_isolation ON scim_tokens AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "DROP POLICY IF EXISTS scim_tokens_by_presented_hash ON scim_tokens",
    "CREATE POLICY scim_tokens_by_presented_hash ON scim_tokens AS PERMISSIVE FOR SELECT USING (COALESCE(current_setting('app.scim_token_hash', true), '') <> '' AND token_hash = current_setting('app.scim_token_hash', true))",
    "ALTER TABLE tenant_mfa ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE tenant_mfa FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_mfa_isolation ON tenant_mfa",
    "CREATE POLICY tenant_mfa_isolation ON tenant_mfa AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE tenant_settings FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_settings_isolation ON tenant_settings",
    "CREATE POLICY tenant_settings_isolation ON tenant_settings AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE whitelabel_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE whitelabel_configs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS whitelabel_configs_isolation ON whitelabel_configs",
    "CREATE POLICY whitelabel_configs_isolation ON whitelabel_configs AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE scope_grants ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE scope_grants FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS scope_grants_isolation ON scope_grants",
    "CREATE POLICY scope_grants_isolation ON scope_grants AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE vault_key_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE vault_key_versions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS vault_key_versions_platform_only ON vault_key_versions",
    "CREATE POLICY vault_key_versions_platform_only ON vault_key_versions AS PERMISSIVE FOR ALL USING (false) WITH CHECK (false)",
    "ALTER TABLE compliance_certifications ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE compliance_certifications FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS compliance_certifications_tenant_read ON compliance_certifications",
    "CREATE POLICY compliance_certifications_tenant_read ON compliance_certifications AS PERMISSIVE FOR SELECT USING (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE consent_records FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS consent_records_tenant_isolation ON consent_records",
    "CREATE POLICY consent_records_tenant_isolation ON consent_records AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE deleted_tenants ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE deleted_tenants FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS deleted_tenants_tenant_isolation ON deleted_tenants",
    "CREATE POLICY deleted_tenants_tenant_read ON deleted_tenants FOR SELECT USING (tenant_id = current_setting('app.tenant_id', true))",
    "CREATE POLICY deleted_tenants_tenant_request ON deleted_tenants FOR INSERT WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "CREATE POLICY deleted_tenants_tenant_complete ON deleted_tenants FOR DELETE USING (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE enterprise_contracts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE enterprise_contracts FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS enterprise_contracts_tenant_isolation ON enterprise_contracts",
    "CREATE POLICY enterprise_contracts_tenant_isolation ON enterprise_contracts AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE gdpr_export_jobs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE gdpr_export_jobs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS gdpr_export_jobs_tenant_isolation ON gdpr_export_jobs",
    "CREATE POLICY gdpr_export_jobs_tenant_isolation ON gdpr_export_jobs AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE golden_tasks ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE golden_tasks FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS golden_tasks_tenant_isolation ON golden_tasks",
    "CREATE POLICY golden_tasks_tenant_isolation ON golden_tasks AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE marketplace_author_accounts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_author_accounts FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS marketplace_author_accounts_tenant_isolation ON marketplace_author_accounts",
    "CREATE POLICY marketplace_author_accounts_tenant_isolation ON marketplace_author_accounts AS PERMISSIVE FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE org_blueprints ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE org_blueprints FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS org_blueprints_read ON org_blueprints",
    "CREATE POLICY org_blueprints_read ON org_blueprints AS PERMISSIVE FOR SELECT USING (tenant_id IS NULL OR tenant_id = app_current_tenant_uuid())",
    "DROP POLICY IF EXISTS org_blueprints_tenant_insert ON org_blueprints",
    "CREATE POLICY org_blueprints_tenant_insert ON org_blueprints AS PERMISSIVE FOR INSERT WITH CHECK (tenant_id = app_current_tenant_uuid())",
    "DROP POLICY IF EXISTS org_blueprints_tenant_update ON org_blueprints",
    "CREATE POLICY org_blueprints_tenant_update ON org_blueprints AS PERMISSIVE FOR UPDATE USING (tenant_id = app_current_tenant_uuid()) WITH CHECK (tenant_id = app_current_tenant_uuid())",
    "DROP POLICY IF EXISTS org_blueprints_tenant_delete ON org_blueprints",
    "CREATE POLICY org_blueprints_tenant_delete ON org_blueprints AS PERMISSIVE FOR DELETE USING (tenant_id = app_current_tenant_uuid())",
    "ALTER TABLE episodic_memories ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE episodic_memories FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS episodic_memories_tenant_isolation ON episodic_memories",
    "CREATE POLICY episodic_memories_tenant_isolation ON episodic_memories\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE procedural_memories ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE procedural_memories FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS procedural_memories_tenant_isolation ON procedural_memories",
    "CREATE POLICY procedural_memories_tenant_isolation ON procedural_memories\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE memory_conflicts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE memory_conflicts FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS memory_conflicts_tenant_isolation ON memory_conflicts",
    "CREATE POLICY memory_conflicts_tenant_isolation ON memory_conflicts\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE reflexion_lessons ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE reflexion_lessons FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS reflexion_lessons_tenant_isolation ON reflexion_lessons",
    "CREATE POLICY reflexion_lessons_tenant_isolation ON reflexion_lessons\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE tool_reliability_memory ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE tool_reliability_memory FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tool_reliability_memory_tenant_isolation ON tool_reliability_memory",
    "CREATE POLICY tool_reliability_memory_tenant_isolation ON tool_reliability_memory\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE tool_trust_records ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE tool_trust_records FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tool_trust_records_tenant_isolation ON tool_trust_records",
    "CREATE POLICY tool_trust_records_tenant_isolation ON tool_trust_records\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE agent_optimization_history ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE agent_optimization_history FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS agent_optimization_history_tenant_isolation ON agent_optimization_history",
    "CREATE POLICY agent_optimization_history_tenant_isolation ON agent_optimization_history\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE improvement_experiments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE improvement_experiments FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS improvement_experiments_tenant_isolation ON improvement_experiments",
    "CREATE POLICY improvement_experiments_tenant_isolation ON improvement_experiments\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE improvement_results ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE improvement_results FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS improvement_results_tenant_isolation ON improvement_results",
    "CREATE POLICY improvement_results_tenant_isolation ON improvement_results\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE self_improvement_actions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE self_improvement_actions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS self_improvement_actions_tenant_isolation ON self_improvement_actions",
    "CREATE POLICY self_improvement_actions_tenant_isolation ON self_improvement_actions\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE agent_connector_credentials ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE agent_connector_credentials FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS agent_connector_credentials_tenant_isolation ON agent_connector_credentials",
    "CREATE POLICY agent_connector_credentials_tenant_isolation ON agent_connector_credentials\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE debate_sessions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE debate_sessions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS debate_sessions_tenant_isolation ON debate_sessions",
    "CREATE POLICY debate_sessions_tenant_isolation ON debate_sessions\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "ALTER TABLE debate_proposals ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE debate_proposals FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS debate_proposals_tenant_isolation ON debate_proposals",
    "CREATE POLICY debate_proposals_tenant_isolation ON debate_proposals\n  USING (tenant_id = current_setting('app.tenant_id', true))\n  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
]

_CHAT_SIBLINGS = ("chat_sessions", "chat_messages")
_PARTITIONED = ("cost_ledger", "audit_events", "policy_evaluations", "guardrail_violations")

_APPLY_PARENT_RLS = r"""
CREATE OR REPLACE FUNCTION app_apply_parent_rls(child regclass) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    parent regclass;
    pol record;
    cmd text;
BEGIN
    SELECT i.inhparent INTO parent FROM pg_inherits i WHERE i.inhrelid = child;
    IF parent IS NULL THEN
        RETURN;
    END IF;
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', child);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', child);
    FOR pol IN
        SELECT p.polname, p.polcmd, p.polpermissive,
               pg_get_expr(p.polqual, p.polrelid) AS qual,
               pg_get_expr(p.polwithcheck, p.polrelid) AS wcheck
        FROM pg_policy p WHERE p.polrelid = parent
    LOOP
        cmd := CASE pol.polcmd WHEN 'r' THEN 'SELECT' WHEN 'a' THEN 'INSERT'
               WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' ELSE 'ALL' END;
        EXECUTE format('DROP POLICY IF EXISTS %I ON %s', pol.polname, child);
        EXECUTE format(
            'CREATE POLICY %I ON %s AS %s FOR %s %s %s',
            pol.polname, child,
            CASE WHEN pol.polpermissive THEN 'PERMISSIVE' ELSE 'RESTRICTIVE' END,
            cmd,
            CASE WHEN pol.qual IS NOT NULL THEN 'USING (' || pol.qual || ')' ELSE '' END,
            CASE WHEN pol.wcheck IS NOT NULL THEN 'WITH CHECK (' || pol.wcheck || ')' ELSE '' END
        );
    END LOOP;
END;
$$
"""


def _target_table(stmt: str) -> str:
    """Table an ALTER TABLE / CREATE|DROP POLICY / CREATE INDEX statement acts on."""
    m = re.search(r"\bON\s+([a-z_0-9]+)", stmt) or re.search(r"ALTER TABLE\s+([a-z_0-9]+)", stmt)
    return m.group(1) if m else ""


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        r[0]
        for r in bind.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).fetchall()
    }
    for stmt in _STATEMENTS:
        # Tables absent in this deployment (optional features) are skipped.
        if _target_table(stmt) not in existing:
            continue
        op.execute(stmt)
    for table in _CHAT_SIBLINGS:
        if table in existing:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_write ON {table}")
            op.execute(
                f"CREATE POLICY {table}_tenant_write ON {table} AS RESTRICTIVE FOR ALL "
                "USING (true) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
            )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_views WHERE viewname = "
        "'chat_session_usage_summary') THEN "
        "EXECUTE 'ALTER VIEW chat_session_usage_summary SET (security_invoker = true)'; "
        "END IF; END $$"
    )
    op.execute(_APPLY_PARENT_RLS)
    for parent in _PARTITIONED:
        if parent not in existing:
            continue
        children = bind.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = CAST(:p AS regclass)"
            ),
            {"p": parent},
        ).fetchall()
        for (child,) in children:
            op.execute(f"SELECT app_apply_parent_rls('{child}'::regclass)")


def downgrade() -> None:
    # Re-opening tenant tables is never the safe direction; keep the guards.
    pass
