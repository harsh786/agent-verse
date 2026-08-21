"""Workflow Engine Tables — 9 new tables for the workflow execution engine.

Adds alongside the existing `workflows` table (from 0046) without touching it.
All tables: RLS enabled, tenant_id = current_setting('app.tenant_id').
"""

from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. system_workflow_templates (read-only, seeded by AgentVerse) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS system_workflow_templates (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            category            TEXT NOT NULL,
            subcategory         TEXT,
            description         TEXT,
            complexity          TEXT NOT NULL DEFAULT 'intermediate',
            tags                TEXT[] DEFAULT '{}',
            required_connectors TEXT[] DEFAULT '{}',
            optional_connectors TEXT[] DEFAULT '{}',
            definition_yaml     TEXT NOT NULL,
            definition_json     JSONB NOT NULL DEFAULT '{}',
            version             TEXT NOT NULL DEFAULT '1.0.0',
            preview_image_url   TEXT,
            sample_input        JSONB,
            sample_output       JSONB,
            popularity          INT DEFAULT 0,
            is_active           BOOL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sys_tmpl_category ON system_workflow_templates (category, is_active)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sys_tmpl_tags ON system_workflow_templates USING gin (tags)"
    )

    # ── 2. workflow_definitions (full — replaces stub from 0046) ─────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_definitions (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                   UUID NOT NULL,
            name                        TEXT NOT NULL,
            slug                        TEXT NOT NULL,
            description                 TEXT,
            tags                        TEXT[] DEFAULT '{}',
            definition_yaml             TEXT NOT NULL DEFAULT '',
            definition_json             JSONB NOT NULL DEFAULT '{}',
            version                     TEXT NOT NULL DEFAULT '1.0.0',
            status                      TEXT NOT NULL DEFAULT 'draft',
            forked_from_template_id     TEXT REFERENCES system_workflow_templates(id),
            forked_from_template_ver    TEXT,
            is_template                 BOOL DEFAULT FALSE,
            trigger_config              JSONB NOT NULL DEFAULT '{}',
            run_retention_days          INT,
            requires_publish_approval   BOOL DEFAULT FALSE,
            publish_approved_by         UUID,
            publish_approved_at         TIMESTAMPTZ,
            publish_approval_note       TEXT,
            created_by                  UUID NOT NULL DEFAULT gen_random_uuid(),
            updated_by                  UUID NOT NULL DEFAULT gen_random_uuid(),
            published_at                TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, slug)
        )
    """)
    op.execute("ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_definitions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY workflow_definitions_tenant ON workflow_definitions
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_def_tenant ON workflow_definitions (tenant_id, status, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_def_tags ON workflow_definitions USING gin (tags)"
    )

    # ── 3. workflow_definition_versions ──────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_definition_versions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workflow_id     UUID NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
            tenant_id       UUID NOT NULL,
            version         TEXT NOT NULL,
            definition_yaml TEXT NOT NULL,
            definition_json JSONB NOT NULL DEFAULT '{}',
            change_summary  TEXT,
            published_by    UUID NOT NULL DEFAULT gen_random_uuid(),
            published_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (workflow_id, version)
        )
    """)
    op.execute("ALTER TABLE workflow_definition_versions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY wf_def_versions_tenant ON workflow_definition_versions
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_def_ver ON workflow_definition_versions (workflow_id, published_at DESC)"
    )

    # ── 4. workflow_runs ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL,
            workflow_id      UUID REFERENCES workflow_definitions(id),
            trigger_type     TEXT NOT NULL DEFAULT 'api',
            trigger_payload  JSONB,
            inputs           JSONB NOT NULL DEFAULT '{}',
            status           TEXT NOT NULL DEFAULT 'pending',
            current_step_id  TEXT,
            outputs          JSONB DEFAULT '{}',
            error            TEXT,
            error_step_id    TEXT,
            cost_usd         DECIMAL(12,6) DEFAULT 0,
            tokens_used      INT DEFAULT 0,
            labels           JSONB DEFAULT '{}',
            run_metadata     JSONB DEFAULT '{}',
            foreach_progress JSONB DEFAULT '{}',
            paused_by        UUID,
            paused_at        TIMESTAMPTZ,
            pause_reason     TEXT,
            is_test_run      BOOL DEFAULT FALSE,
            test_scenario_id UUID,
            started_at       TIMESTAMPTZ,
            completed_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY workflow_runs_tenant ON workflow_runs
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_runs_workflow ON workflow_runs (tenant_id, workflow_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_runs_status ON workflow_runs (tenant_id, status, created_at DESC)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_wf_runs_labels ON workflow_runs USING gin (labels)")

    # ── 5. workflow_step_results ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_step_results (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id         UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
            tenant_id      UUID NOT NULL,
            step_id        TEXT NOT NULL,
            step_type      TEXT NOT NULL,
            step_name      TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            input          JSONB,
            output         JSONB,
            resolved_input JSONB,
            error          TEXT,
            llm_prompt     TEXT,
            llm_response   TEXT,
            tokens_in      INT DEFAULT 0,
            tokens_out     INT DEFAULT 0,
            cost_usd       DECIMAL(12,6) DEFAULT 0,
            duration_ms    INT,
            attempt_number INT DEFAULT 1,
            started_at     TIMESTAMPTZ,
            completed_at   TIMESTAMPTZ
        )
    """)
    op.execute("ALTER TABLE workflow_step_results ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY wf_step_results_tenant ON workflow_step_results
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_step_results_run ON workflow_step_results (run_id, step_id)"
    )

    # ── 6. workflow_hitl_requests ────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_hitl_requests (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL,
            run_id              UUID NOT NULL REFERENCES workflow_runs(id),
            step_id             TEXT NOT NULL,
            step_name           TEXT,
            workflow_name       TEXT,
            assignee_role       TEXT NOT NULL,
            assignee_strategy   TEXT DEFAULT 'round_robin',
            assigned_to         UUID,
            status              TEXT NOT NULL DEFAULT 'pending',
            context_payload     JSONB NOT NULL DEFAULT '[]',
            actions_config      JSONB NOT NULL DEFAULT '[]',
            chosen_action       TEXT,
            reviewer_note       TEXT,
            reviewed_by         UUID,
            reviewed_at         TIMESTAMPTZ,
            delegated_to        UUID,
            delegated_at        TIMESTAMPTZ,
            escalated_to        UUID,
            escalated_at        TIMESTAMPTZ,
            deadline_at         TIMESTAMPTZ NOT NULL,
            escalation_at       TIMESTAMPTZ,
            priority            TEXT DEFAULT 'medium',
            timeout_action      TEXT DEFAULT 'escalate',
            custom_form_schema  JSONB,
            custom_form_data    JSONB,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("ALTER TABLE workflow_hitl_requests ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY wf_hitl_requests_tenant ON workflow_hitl_requests
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wf_hitl_pending
        ON workflow_hitl_requests (tenant_id, status, priority, deadline_at)
        WHERE status = 'pending'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wf_hitl_assignee
        ON workflow_hitl_requests (assigned_to, status)
        WHERE assigned_to IS NOT NULL
    """)

    # ── 7. workflow_test_scenarios ───────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_test_scenarios (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL,
            workflow_id          UUID NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
            name                 TEXT NOT NULL,
            description          TEXT,
            input_fixture        JSONB NOT NULL DEFAULT '{}',
            mock_overrides       JSONB DEFAULT '{}',
            expected_outputs     JSONB,
            expected_steps_reached TEXT[],
            expected_branch      TEXT,
            last_run_id          UUID REFERENCES workflow_runs(id),
            last_run_status      TEXT,
            last_run_at          TIMESTAMPTZ,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("ALTER TABLE workflow_test_scenarios ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY wf_test_scenarios_tenant ON workflow_test_scenarios
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)

    # ── 8. workflow_permissions ──────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_permissions (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workflow_id  UUID NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
            tenant_id    UUID NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id   TEXT NOT NULL,
            permission   TEXT NOT NULL,
            granted_by   UUID NOT NULL DEFAULT gen_random_uuid(),
            granted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (workflow_id, subject_type, subject_id, permission)
        )
    """)
    op.execute("ALTER TABLE workflow_permissions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY wf_permissions_tenant ON workflow_permissions
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)

    # ── 9. workflow_webhook_events (DLQ) ─────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_webhook_events (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL,
            workflow_id      UUID REFERENCES workflow_definitions(id),
            webhook_token    TEXT NOT NULL,
            payload          JSONB NOT NULL DEFAULT '{}',
            headers          JSONB,
            status           TEXT NOT NULL DEFAULT 'pending',
            attempts         INT DEFAULT 0,
            last_error       TEXT,
            run_id           UUID REFERENCES workflow_runs(id),
            received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_attempted_at TIMESTAMPTZ,
            completed_at     TIMESTAMPTZ
        )
    """)
    op.execute("ALTER TABLE workflow_webhook_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY wf_webhook_events_tenant ON workflow_webhook_events
        USING (tenant_id::text = current_setting('app.tenant_id', TRUE))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wf_webhook_pending
        ON workflow_webhook_events (status, received_at)
        WHERE status IN ('pending', 'failed')
    """)


def downgrade() -> None:
    tables = [
        "workflow_webhook_events",
        "workflow_permissions",
        "workflow_test_scenarios",
        "workflow_hitl_requests",
        "workflow_step_results",
        "workflow_runs",
        "workflow_definition_versions",
        "workflow_definitions",
        "system_workflow_templates",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
