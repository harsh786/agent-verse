"""Marketplace: reviewer-only review writes; SECURITY DEFINER counter functions.

Two defects in 0059's RLS, both on the marketplace tables:

* ``marketplace_reviews_rls`` was one ``FOR ALL`` policy whose USING clause let a
  tenant see a review if it wrote it OR the template is public/community — and
  for ``FOR ALL`` the USING clause also governs UPDATE and DELETE. So any tenant
  could rewrite or delete any other tenant's review of a public template. It is
  split into per-command policies: SELECT keeps the old visibility rule; INSERT,
  UPDATE and DELETE are restricted to the reviewer tenant (and INSERT also
  requires the template to be visible to the reviewer).

* ``install_count`` / ``rating_avg`` / ``rating_count`` live on the template
  row, whose write policy only matches the owning tenant. A tenant installing
  or reviewing someone else's template — or any system built-in — ran an
  ``UPDATE marketplace_templates`` that matched 0 rows under RLS, silently, and
  the counters never moved. Two narrow SECURITY DEFINER functions now do those
  updates. Each checks the caller (``app.tenant_id``) may see the template (the
  same rule as the application's visibility check), switches ``app.tenant_id``
  to the template's owner for the single UPDATE — so it works whether or not
  the function owner bypasses RLS — restores the caller's value, and returns
  the number of rows updated so the application can fail loudly on 0.

Revision ID: d2b7e4f1a8c6
Revises: a9c4e2f7b1d3
"""

from __future__ import annotations

from alembic import op

revision = "d2b7e4f1a8c6"
down_revision = "a9c4e2f7b1d3"
branch_labels = None
depends_on = None

_GUC = "current_setting('app.tenant_id', TRUE)"

# Same rule as ``_VISIBLE_SQL`` in app/enterprise/marketplace_v2.py.
_CALLER_CAN_SEE = """
    (t.tenant_id = caller
     OR (t.visibility IN ('public', 'community')
         AND (t.review_status = 'approved'
              OR (t.tenant_id = 'system' AND t.is_builtin))))
"""

_BUMP_INSTALL_COUNT = f"""
CREATE OR REPLACE FUNCTION marketplace_bump_install_count(p_template_id TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    caller TEXT := {_GUC};
    owner_tenant TEXT;
    updated INTEGER;
BEGIN
    IF caller IS NULL OR caller = '' THEN
        RETURN 0;
    END IF;
    SELECT t.tenant_id INTO owner_tenant
    FROM marketplace_templates t
    WHERE t.id = p_template_id AND {_CALLER_CAN_SEE};
    IF owner_tenant IS NULL THEN
        RETURN 0;
    END IF;
    -- Only an actual install by the caller may move the counter.
    IF NOT EXISTS (
        SELECT 1 FROM marketplace_installs i
        WHERE i.template_id = p_template_id
          AND i.installer_tenant_id = caller
          AND i.uninstalled_at IS NULL
    ) THEN
        RETURN 0;
    END IF;
    PERFORM set_config('app.tenant_id', owner_tenant, TRUE);
    UPDATE marketplace_templates
       SET install_count = install_count + 1, updated_at = NOW()
     WHERE id = p_template_id;
    GET DIAGNOSTICS updated = ROW_COUNT;
    PERFORM set_config('app.tenant_id', caller, TRUE);
    RETURN updated;
END;
$$
"""

_REFRESH_RATING = f"""
CREATE OR REPLACE FUNCTION marketplace_refresh_template_rating(p_template_id TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    caller TEXT := {_GUC};
    owner_tenant TEXT;
    updated INTEGER;
BEGIN
    IF caller IS NULL OR caller = '' THEN
        RETURN 0;
    END IF;
    SELECT t.tenant_id INTO owner_tenant
    FROM marketplace_templates t
    WHERE t.id = p_template_id AND {_CALLER_CAN_SEE};
    IF owner_tenant IS NULL THEN
        RETURN 0;
    END IF;
    PERFORM set_config('app.tenant_id', owner_tenant, TRUE);
    UPDATE marketplace_templates SET
        rating_avg = (
            SELECT AVG(r.rating)::float FROM marketplace_reviews r
            WHERE r.template_id = p_template_id
        ),
        rating_count = (
            SELECT COUNT(*) FROM marketplace_reviews r
            WHERE r.template_id = p_template_id
        ),
        updated_at = NOW()
    WHERE id = p_template_id;
    GET DIAGNOSTICS updated = ROW_COUNT;
    PERFORM set_config('app.tenant_id', caller, TRUE);
    RETURN updated;
END;
$$
"""

_FUNCTIONS = (
    "marketplace_bump_install_count(TEXT)",
    "marketplace_refresh_template_rating(TEXT)",
)

_REVIEW_POLICIES = (
    "marketplace_reviews_read",
    "marketplace_reviews_insert",
    "marketplace_reviews_update",
    "marketplace_reviews_delete",
)


def upgrade() -> None:
    op.execute("ALTER TABLE marketplace_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE marketplace_reviews FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS marketplace_reviews_rls ON marketplace_reviews")
    for policy in _REVIEW_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON marketplace_reviews")
    op.execute(f"""
        CREATE POLICY marketplace_reviews_read ON marketplace_reviews
        FOR SELECT
        USING (
            reviewer_tenant_id = {_GUC}
            OR EXISTS (
                SELECT 1 FROM marketplace_templates t
                WHERE t.id = template_id
                  AND t.visibility IN ('public','community')
            )
        )
    """)
    op.execute(f"""
        CREATE POLICY marketplace_reviews_insert ON marketplace_reviews
        FOR INSERT
        WITH CHECK (
            reviewer_tenant_id = {_GUC}
            AND EXISTS (SELECT 1 FROM marketplace_templates t WHERE t.id = template_id)
        )
    """)
    op.execute(f"""
        CREATE POLICY marketplace_reviews_update ON marketplace_reviews
        FOR UPDATE
        USING (reviewer_tenant_id = {_GUC})
        WITH CHECK (reviewer_tenant_id = {_GUC})
    """)
    op.execute(f"""
        CREATE POLICY marketplace_reviews_delete ON marketplace_reviews
        FOR DELETE
        USING (reviewer_tenant_id = {_GUC})
    """)

    op.execute(_BUMP_INSTALL_COUNT)
    op.execute(_REFRESH_RATING)


def downgrade() -> None:
    for fn in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for policy in _REVIEW_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON marketplace_reviews")
    # Restore 0059's single FOR ALL policy (RLS stays ENABLE + FORCE, as 0059 set it).
    op.execute(f"""
        CREATE POLICY marketplace_reviews_rls ON marketplace_reviews
        FOR ALL
        USING (
            reviewer_tenant_id = {_GUC}
            OR EXISTS (
                SELECT 1 FROM marketplace_templates t
                WHERE t.id = template_id
                  AND t.visibility IN ('public','community')
            )
        )
        WITH CHECK (reviewer_tenant_id = {_GUC})
    """)
