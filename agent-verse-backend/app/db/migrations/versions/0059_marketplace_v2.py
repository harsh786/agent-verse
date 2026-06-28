"""marketplace_templates, marketplace_reviews, marketplace_installs,
marketplace_security_reviews, marketplace_template_versions

Revision ID: 0059
Revises: 0057
Create Date: 2026-06-28

Creates the full marketplace DB schema:
  - marketplace_templates  : persisted template catalog (TEXT ids, full-text + vector search)
  - marketplace_reviews    : per-tenant ratings/reviews (1-5 stars, verified install flag)
  - marketplace_installs   : atomic install tracking (fixes ghost-agent bug)
  - marketplace_security_reviews : automated security review pipeline results
  - marketplace_template_versions: version history snapshots

RLS Notes:
  - marketplace_templates: public/community templates visible to all tenants;
    private templates visible only to owning tenant.
  - All other tables: strict per-tenant isolation via app.tenant_id GUC.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension (idempotent — may already exist from 0024)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # marketplace_templates
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_templates (
            id                TEXT PRIMARY KEY DEFAULT (
                               lower(substr(replace(gen_random_uuid()::text,'-',''),1,32))
                             ),
            tenant_id         TEXT NOT NULL,
            name              TEXT NOT NULL,
            slug              TEXT NOT NULL,
            description       TEXT NOT NULL DEFAULT '',
            long_description  TEXT NOT NULL DEFAULT '',
            domain            TEXT NOT NULL DEFAULT 'general',
            subdomain         TEXT,
            category          TEXT,
            tags              TEXT[] NOT NULL DEFAULT '{}',
            template_config   JSONB NOT NULL DEFAULT '{}',
            parameters_schema JSONB NOT NULL DEFAULT '{}',
            required_connectors TEXT[] NOT NULL DEFAULT '{}',
            optional_connectors TEXT[] NOT NULL DEFAULT '{}',
            author_name       TEXT NOT NULL DEFAULT '',
            icon_url          TEXT,
            visibility        TEXT NOT NULL DEFAULT 'private'
                                CHECK (visibility IN ('private','team','community','public')),
            review_status     TEXT NOT NULL DEFAULT 'unreviewed'
                                CHECK (review_status IN
                                       ('unreviewed','pending','approved','rejected')),
            review_notes      TEXT,
            is_builtin        BOOLEAN NOT NULL DEFAULT FALSE,
            is_verified       BOOLEAN NOT NULL DEFAULT FALSE,
            install_count     INTEGER NOT NULL DEFAULT 0,
            rating_avg        FLOAT,
            rating_count      INTEGER NOT NULL DEFAULT 0,
            version           TEXT NOT NULL DEFAULT '1.0.0',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_marketplace_templates_slug "
        "ON marketplace_templates(slug)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_templates_domain "
        "ON marketplace_templates(domain, review_status, visibility)"
    )
    # Full-text search index
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_marketplace_templates_fts
        ON marketplace_templates
        USING gin(
            to_tsvector('english',
                coalesce(name,'') || ' ' ||
                coalesce(description,'') || ' ' ||
                array_to_string(tags, ' ')
            )
        )
    """)
    # Optional: vector embedding column for semantic search (768-dim, same as LTM)
    op.execute(
        "ALTER TABLE marketplace_templates "
        "ADD COLUMN IF NOT EXISTS embedding vector(768)"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_marketplace_templates_vec
        ON marketplace_templates
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        WHERE embedding IS NOT NULL AND review_status = 'approved'
    """)

    # RLS: public/community templates readable by all; private only by owning tenant
    op.execute("ALTER TABLE marketplace_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE marketplace_templates FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY marketplace_templates_read ON marketplace_templates
        FOR SELECT
        USING (
            visibility IN ('public', 'community')
            OR tenant_id = current_setting('app.tenant_id', TRUE)
        )
    """)
    op.execute("""
        CREATE POLICY marketplace_templates_write ON marketplace_templates
        FOR ALL
        USING    (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)

    # ------------------------------------------------------------------
    # marketplace_reviews
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_reviews (
            id                   TEXT PRIMARY KEY DEFAULT (
                                  lower(substr(replace(gen_random_uuid()::text,'-',''),1,32))
                                 ),
            template_id          TEXT NOT NULL
                                  REFERENCES marketplace_templates(id) ON DELETE CASCADE,
            reviewer_tenant_id   TEXT NOT NULL,
            rating               SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            title                TEXT NOT NULL DEFAULT '',
            body                 TEXT NOT NULL DEFAULT '',
            helpful_count        INTEGER NOT NULL DEFAULT 0,
            verified_install     BOOLEAN NOT NULL DEFAULT FALSE,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_marketplace_review_per_tenant
                UNIQUE (template_id, reviewer_tenant_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_reviews_template "
        "ON marketplace_reviews(template_id, created_at DESC)"
    )
    op.execute("ALTER TABLE marketplace_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE marketplace_reviews FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY marketplace_reviews_rls ON marketplace_reviews
        FOR ALL
        USING (
            reviewer_tenant_id = current_setting('app.tenant_id', TRUE)
            OR EXISTS (
                SELECT 1 FROM marketplace_templates t
                WHERE t.id = template_id
                  AND t.visibility IN ('public','community')
            )
        )
        WITH CHECK (reviewer_tenant_id = current_setting('app.tenant_id', TRUE))
    """)

    # ------------------------------------------------------------------
    # marketplace_installs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_installs (
            id                 TEXT PRIMARY KEY DEFAULT (
                                lower(substr(replace(gen_random_uuid()::text,'-',''),1,32))
                               ),
            template_id        TEXT NOT NULL
                                REFERENCES marketplace_templates(id) ON DELETE CASCADE,
            installer_tenant_id TEXT NOT NULL,
            agent_id           TEXT,
            parameters         JSONB NOT NULL DEFAULT '{}',
            installed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            uninstalled_at     TIMESTAMPTZ,
            install_status     TEXT NOT NULL DEFAULT 'success'
                                CHECK (install_status IN ('success','failed','pending')),
            install_error      TEXT,
            CONSTRAINT uq_marketplace_install_per_tenant
                UNIQUE (template_id, installer_tenant_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_installs_tenant "
        "ON marketplace_installs(installer_tenant_id, installed_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_installs_template "
        "ON marketplace_installs(template_id)"
    )
    op.execute("ALTER TABLE marketplace_installs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE marketplace_installs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY marketplace_installs_rls ON marketplace_installs
        FOR ALL
        USING    (installer_tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (installer_tenant_id = current_setting('app.tenant_id', TRUE))
    """)

    # ------------------------------------------------------------------
    # marketplace_security_reviews
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_security_reviews (
            id           TEXT PRIMARY KEY DEFAULT (
                          lower(substr(replace(gen_random_uuid()::text,'-',''),1,32))
                         ),
            template_id  TEXT NOT NULL
                          REFERENCES marketplace_templates(id) ON DELETE CASCADE,
            reviewer_id  TEXT,
            findings     JSONB NOT NULL DEFAULT '[]',
            risk_level   TEXT NOT NULL DEFAULT 'safe'
                          CHECK (risk_level IN ('safe','low','medium','high','critical')),
            approved     BOOLEAN NOT NULL DEFAULT FALSE,
            reviewed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_sec_reviews_template "
        "ON marketplace_security_reviews(template_id, reviewed_at DESC)"
    )
    op.execute("ALTER TABLE marketplace_security_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE marketplace_security_reviews FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY marketplace_security_reviews_rls ON marketplace_security_reviews
        FOR ALL
        USING    (reviewer_id = current_setting('app.tenant_id', TRUE)
                  OR EXISTS (
                      SELECT 1 FROM marketplace_templates t
                      WHERE t.id = template_id
                        AND t.tenant_id = current_setting('app.tenant_id', TRUE)
                  ))
        WITH CHECK (reviewer_id = current_setting('app.tenant_id', TRUE)
                    OR EXISTS (
                        SELECT 1 FROM marketplace_templates t
                        WHERE t.id = template_id
                          AND t.tenant_id = current_setting('app.tenant_id', TRUE)
                    ))
    """)

    # ------------------------------------------------------------------
    # marketplace_template_versions
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_template_versions (
            id          TEXT PRIMARY KEY DEFAULT (
                         lower(substr(replace(gen_random_uuid()::text,'-',''),1,32))
                        ),
            template_id TEXT NOT NULL
                         REFERENCES marketplace_templates(id) ON DELETE CASCADE,
            tenant_id   TEXT NOT NULL,
            version     TEXT NOT NULL,
            config      JSONB NOT NULL DEFAULT '{}',
            reason      TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_marketplace_template_versions_template "
        "ON marketplace_template_versions(template_id, created_at DESC)"
    )
    op.execute("ALTER TABLE marketplace_template_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE marketplace_template_versions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY marketplace_template_versions_rls ON marketplace_template_versions
        FOR ALL
        USING    (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marketplace_template_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS marketplace_security_reviews CASCADE")
    op.execute("DROP TABLE IF EXISTS marketplace_installs CASCADE")
    op.execute("DROP TABLE IF EXISTS marketplace_reviews CASCADE")
    op.execute("DROP TABLE IF EXISTS marketplace_templates CASCADE")
