"""Migration d2b7e4f1a8c6: reviewer-only review writes + counter functions.

0059's ``marketplace_reviews_rls`` was a single ``FOR ALL`` policy whose USING
clause admitted any tenant for reviews of public/community templates — and for
``FOR ALL`` that USING clause governs UPDATE and DELETE too, so any tenant
could rewrite or delete another tenant's review. These tests pin the shape of
the replacement policies without a database (the integration test in
tests/enterprise/test_marketplace_rls_integration.py exercises them for real).
"""

from __future__ import annotations

import importlib
import inspect
import re
from typing import Any

import pytest

_MOD = "app.db.migrations.versions.d2b7e4f1a8c6_marketplace_reviews_rls_and_counters"
_GUC = "current_setting('app.tenant_id', TRUE)"


def _run(fn_name: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    module = importlib.import_module(_MOD)
    executed: list[str] = []

    class _Op:
        def execute(self, sql: Any) -> None:
            executed.append(" ".join(str(sql).split()))

    monkeypatch.setattr(module, "op", _Op())
    getattr(module, fn_name)()
    return executed


def _policy(stmts: list[str], name: str) -> str:
    found = [s for s in stmts if s.startswith(f"CREATE POLICY {name} ")]
    assert len(found) == 1, (name, found)
    return found[0]


def test_revision_chain() -> None:
    module = importlib.import_module(_MOD)
    assert module.revision == "d2b7e4f1a8c6"
    assert module.down_revision == "b3d5f7a9c1e2"


def test_upgrade_drops_the_for_all_policy_and_keeps_rls_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stmts = _run("upgrade", monkeypatch)
    assert "ALTER TABLE marketplace_reviews ENABLE ROW LEVEL SECURITY" in stmts
    assert "ALTER TABLE marketplace_reviews FORCE ROW LEVEL SECURITY" in stmts
    assert "DROP POLICY IF EXISTS marketplace_reviews_rls ON marketplace_reviews" in stmts
    # No FOR ALL policy remains on reviews after upgrade.
    creates = [s for s in stmts if s.startswith("CREATE POLICY") and "marketplace_reviews" in s]
    assert creates and not any("FOR ALL" in s for s in creates)


def test_update_and_delete_are_reviewer_only(monkeypatch: pytest.MonkeyPatch) -> None:
    stmts = _run("upgrade", monkeypatch)
    reviewer_only = f"reviewer_tenant_id = {_GUC}"

    update = _policy(stmts, "marketplace_reviews_update")
    assert "FOR UPDATE" in update
    assert f"USING ({reviewer_only})" in update
    assert f"WITH CHECK ({reviewer_only})" in update
    assert "visibility" not in update and " OR " not in update

    delete = _policy(stmts, "marketplace_reviews_delete")
    assert "FOR DELETE" in delete
    assert f"USING ({reviewer_only})" in delete
    assert "visibility" not in delete and " OR " not in delete

    insert = _policy(stmts, "marketplace_reviews_insert")
    assert "FOR INSERT" in insert
    assert reviewer_only in insert
    assert " OR " not in insert


def test_read_keeps_the_existing_visibility_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    read = _policy(_run("upgrade", monkeypatch), "marketplace_reviews_read")
    assert "FOR SELECT" in read
    assert f"reviewer_tenant_id = {_GUC}" in read
    assert "t.visibility IN ('public','community')" in read


def test_upgrade_creates_security_definer_counter_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stmts = _run("upgrade", monkeypatch)
    fns = [s for s in stmts if s.startswith("CREATE OR REPLACE FUNCTION")]
    names = {re.match(r"CREATE OR REPLACE FUNCTION (\w+)", s).group(1) for s in fns}  # type: ignore[union-attr]
    assert names == {"marketplace_bump_install_count", "marketplace_refresh_template_rating"}
    for fn in fns:
        assert "SECURITY DEFINER" in fn
        assert "SET search_path = pg_catalog, public" in fn
        assert "RETURNS INTEGER" in fn
        # Owner-context switch is undone before returning.
        assert fn.count("set_config('app.tenant_id'") == 2
        assert "PERFORM set_config('app.tenant_id', caller, TRUE)" in fn


def test_downgrade_restores_0059_policy_and_drops_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stmts = _run("downgrade", monkeypatch)
    for name in (
        "marketplace_reviews_read",
        "marketplace_reviews_insert",
        "marketplace_reviews_update",
        "marketplace_reviews_delete",
    ):
        assert f"DROP POLICY IF EXISTS {name} ON marketplace_reviews" in stmts
    assert "DROP FUNCTION IF EXISTS marketplace_bump_install_count(TEXT)" in stmts
    assert "DROP FUNCTION IF EXISTS marketplace_refresh_template_rating(TEXT)" in stmts
    restored = _policy(stmts, "marketplace_reviews_rls")
    assert "FOR ALL" in restored
    assert f"WITH CHECK (reviewer_tenant_id = {_GUC})" in restored


def test_is_on_the_single_alembic_head_lineage() -> None:
    """One head, and this migration is part of its history (later migrations
    may chain on top of it)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = importlib.import_module(_MOD)
    backend_root = inspect.getfile(module).split("/app/db/")[0]
    script = ScriptDirectory.from_config(Config(f"{backend_root}/alembic.ini"))
    heads = script.get_heads()
    assert len(heads) == 1, heads
    lineage = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert "d2b7e4f1a8c6" in lineage
