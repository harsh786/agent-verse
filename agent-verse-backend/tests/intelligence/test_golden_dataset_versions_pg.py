"""MEM-54 against real Postgres: versioned golden datasets under RLS.

* the migration moves a suite's legacy JSON tasks into revision rows (v1);
* a 10k-task dataset is imported as one version and paged back;
* edits/deletes are copy-on-write and past versions stay readable;
* closed (published) revisions are immutable (trigger);
* a tenant never sees another tenant's dataset (NOBYPASSRLS app role).
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.intelligence._eval_pg import alembic, eval_postgres, provision_app_role

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_versioned_dataset_lifecycle_under_rls() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.intelligence.eval_suite_store import EvalSuiteStore

    with eval_postgres(upgrade_to="cf87de8eae52") as admin_url:
        admin_engine = create_async_engine(admin_url)
        admin = async_sessionmaker(admin_engine, expire_on_commit=False)
        legacy_tenant = f"t-{uuid.uuid4().hex[:8]}"
        legacy_tasks = [
            {"task_id": "L1", "goal": "legacy one", "expected_tools": ["kb.search"],
             "forbidden_tools": [], "expected_output_contains": ["ok"], "max_iterations": 7,
             "tags": ["smoke"]},
            {"task_id": "L2", "goal": "legacy two", "expected_tools": [],
             "forbidden_tools": ["db.drop"], "expected_output_contains": []},
        ]
        async with admin() as s, s.begin():
            await s.execute(
                text("INSERT INTO eval_suites (id, tenant_id, name, tasks) "
                     "VALUES ('legacy', :t, 'legacy', CAST(:tasks AS json))"),
                {"t": legacy_tenant, "tasks": json.dumps(legacy_tasks)},
            )
        alembic(admin_url, "head")
        await admin_engine.dispose()
        admin_engine = create_async_engine(admin_url)
        admin = async_sessionmaker(admin_engine, expire_on_commit=False)

        app_url = await provision_app_role(admin_url)
        app_engine = create_async_engine(app_url)
        app = async_sessionmaker(app_engine, expire_on_commit=False)
        try:
            # ── backfill ────────────────────────────────────────────────
            legacy = EvalSuiteStore(app, legacy_tenant)
            meta = await legacy.get_meta("legacy")
            assert meta is not None and meta["dataset_version"] == 1
            tasks = await legacy.list_tasks("legacy")
            assert [(t["task_id"], t["goal"]) for t in tasks] == [
                ("L1", "legacy one"), ("L2", "legacy two")
            ]
            assert tasks[0]["expected_output_contains"] == ["ok"]
            assert tasks[0]["max_iterations"] == 7 and tasks[1]["forbidden_tools"] == ["db.drop"]

            # ── 10k import, paged ───────────────────────────────────────
            a, b = f"a-{uuid.uuid4().hex[:8]}", f"b-{uuid.uuid4().hex[:8]}"
            store = EvalSuiteStore(app, a)
            assert await store.create("big", name="big", description="") is not None
            result = await store.import_tasks(
                "big",
                [{"goal": f"goal {i}", "expected_tools": ["t"]} for i in range(10_000)],
                replace=False,
            )
            assert result is not None and result["dataset_version"] == 1
            seen = [t async for t in store.iter_tasks("big", 1)]
            assert len(seen) == 10_000 and seen[0]["goal"] == "goal 0"
            assert seen[-1]["goal"] == "goal 9999"
            assert (await store.get_meta("big") or {})["task_count"] == 10_000

            # ── copy-on-write edit / delete ─────────────────────────────
            first, second = seen[0]["task_id"], seen[1]["task_id"]
            edited = await store.update_task("big", first, {"goal": "goal 0 edited"})
            assert edited is not None and edited["dataset_version"] == 2
            assert await store.delete_task("big", second) == 3
            v1 = await store.list_tasks("big", version=1, limit=2)
            v3 = await store.list_tasks("big", version=3, limit=2)
            assert [t["goal"] for t in v1] == ["goal 0", "goal 1"]
            assert [t["goal"] for t in v3] == ["goal 0 edited", "goal 2"]

            # ── published revisions are immutable ───────────────────────
            async with admin() as s:
                with pytest.raises(Exception, match="immutable"):
                    async with s.begin():
                        await s.execute(
                            text("UPDATE golden_tasks SET goal = 'tampered' "
                                 "WHERE tenant_id = :t AND task_id = :task "
                                 "AND valid_to IS NOT NULL"),
                            {"t": a, "task": first},
                        )
            async with admin() as s:
                with pytest.raises(Exception, match="only be closed"):
                    async with s.begin():
                        await s.execute(
                            text("UPDATE golden_tasks SET goal = 'tampered' "
                                 "WHERE tenant_id = :t AND task_id = :task "
                                 "AND valid_to IS NULL"),
                            {"t": a, "task": first},
                        )

            # ── runs record the version ─────────────────────────────────
            await store.start_run("big", "run1", 9_999, dataset_version=3)
            (run,) = await store.list_runs("big")
            assert run["dataset_version"] == 3

            # ── tenant isolation ────────────────────────────────────────
            other = EvalSuiteStore(app, b)
            assert await other.get_meta("big") is None
            assert await other.list_tasks("big", version=3) == []
            assert await other.update_task("big", first, {"goal": "hijack"}) is None
            assert await other.delete_task("big", first) is None
            assert await other.import_tasks("big", [{"goal": "x", "expected_tools": ["y"]}],
                                            replace=True) is None
            async with app() as s, s.begin():
                await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": b})
                n = (await s.execute(text("SELECT count(*) FROM golden_tasks"))).scalar()
            assert n == 0
        finally:
            await app_engine.dispose()
            await admin_engine.dispose()
