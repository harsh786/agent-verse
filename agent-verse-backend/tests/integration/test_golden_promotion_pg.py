"""a10-F235-01 on real Postgres: promote a goal to a golden task, run the suite.

* ``POST /intelligence/eval-suites/{id}/tasks/from-goal/{goal_id}`` as the
  NOBYPASSRLS app role writes a ``golden_tasks`` revision (expected answer,
  tools, citations, ``source_goal_id``) as a new dataset version, with a
  durable ``audit_log`` row; another tenant cannot promote into the suite.
* A durable run of that dataset version executes the promoted task and scores
  it against the promoted expectation.
* Migration f7d9b1c3e5a8 refuses to drop the never-read ``golden_datasets`` /
  ``golden_dataset_items`` while they hold rows unless
  ``AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP=1``; its downgrade restores them.

Run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integration/test_golden_promotion_pg.py -m integration --no-cov
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from tests.intelligence._eval_fakes import FakeGoals, FastSettings
from tests.intelligence._eval_pg import alembic, eval_postgres, provision_app_role
from tests.intelligence.test_golden_promotion import PromotableGoals

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ANSWER = "Restart the ingest worker, then replay the DLQ."


class _Rerun(FakeGoals):
    """A suite rerun that reproduces the promoted run: same tool, citation, answer."""

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        return [
            {"type": "knowledge_retrieved", "citations": [{"source": "runbook.md"}]},
            {"type": "synthesis_complete", "citations": [{"source": "postmortem-42"}]},
            {"type": "tool_call_complete", "tool_name": "kb.search", "output": "runbook"},
            {"type": "step_complete", "output": ANSWER},
            {"type": "goal_complete"},
        ]


async def _tables(admin: Any) -> set[str]:
    async with admin() as s:
        rows = await s.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE tablename IN "
                "('golden_datasets', 'golden_dataset_items')"
            )
        )
        return set(rows.scalars())


async def test_unused_table_drop_is_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.delenv("AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP", raising=False)
    with eval_postgres(upgrade_to="e6c8a0b2d4f7") as admin_url:
        engine = create_async_engine(admin_url)
        admin = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with admin() as s, s.begin():
                await s.execute(
                    text(
                        "INSERT INTO golden_datasets (id, tenant_id, name) "
                        "VALUES ('d1', 't1', 'legacy')"
                    )
                )
            with pytest.raises(RuntimeError, match="Refusing to drop unused tables"):
                alembic(admin_url, "head")
            assert await _tables(admin) == {"golden_datasets", "golden_dataset_items"}

            monkeypatch.setenv("AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP", "1")
            alembic(admin_url, "head")
            assert await _tables(admin) == set()

            monkeypatch.delenv("AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP")
            alembic(admin_url, "e6c8a0b2d4f7", command="downgrade")
            assert await _tables(admin) == {"golden_datasets", "golden_dataset_items"}
            alembic(admin_url, "head")  # empty tables: no opt-in needed
            assert await _tables(admin) == set()
        finally:
            await engine.dispose()


async def test_promote_then_run_on_the_app_role() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.enterprise import intelligence_router
    from app.governance.audit import AuditLog
    from app.intelligence.eval_suite import EvalSuiteRunner
    from app.intelligence.eval_suite_jobs import RunSettings, run_until_done
    from app.intelligence.eval_suite_store import EvalSuiteStore
    from app.tenancy.context import PlanTier, TenantContext

    owner, intruder = f"t-{uuid.uuid4().hex[:10]}", f"t-{uuid.uuid4().hex[:10]}"
    with eval_postgres() as admin_url:
        admin_engine = create_async_engine(admin_url)
        admin = async_sessionmaker(admin_engine, expire_on_commit=False)
        async with admin() as s, s.begin():
            for tid in (owner, intruder):
                await s.execute(
                    text(
                        "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                        "VALUES (:t, :t, :e, 'professional', true)"
                    ),
                    {"t": tid, "e": f"{tid}@example.test"},
                )
        app_engine = create_async_engine(await provision_app_role(admin_url))
        app_db = async_sessionmaker(app_engine, expire_on_commit=False)

        def _client(tenant_id: str) -> httpx.AsyncClient:
            api = FastAPI()
            api.include_router(intelligence_router)
            api.state.db_session_factory = app_db
            api.state.eval_suite_runner = EvalSuiteRunner()
            api.state.audit_log = AuditLog(db_session_factory=app_db)
            api.state.goal_service = PromotableGoals()

            @api.middleware("http")
            async def _inject(request: Any, call_next: Any) -> Any:
                request.state.tenant = TenantContext(
                    tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k-owner"
                )
                return await call_next(request)

            return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t")

        try:
            async with _client(owner) as c, _client(intruder) as other:
                sid = (await c.post("/intelligence/eval-suites", json={"name": "ops"})).json()[
                    "suite_id"
                ]
                url = f"/intelligence/eval-suites/{sid}/tasks/from-goal/g-done"
                assert (await other.post(url)).status_code == 404  # not the intruder's suite
                r = await c.post(url, json={"tags": ["incident"]})
                assert r.status_code == 201, r.text
                assert r.json()["dataset_version"] == 1

            async with admin() as s:
                row = (
                    await s.execute(
                        text(
                            "SELECT goal, expected_output, expected_tool_calls, "
                            "expected_citations, source_goal_id, valid_from FROM golden_tasks "
                            "WHERE tenant_id = :t AND eval_suite_id = :s"
                        ),
                        {"t": owner, "s": sid},
                    )
                ).one()
                audit = (
                    await s.execute(
                        text(
                            "SELECT outcome, goal_id FROM audit_log WHERE tenant_id = :t "
                            "AND tool_name = 'eval.golden_task.promote'"
                        ),
                        {"t": owner},
                    )
                ).all()
            assert row[0] == "Fix the stuck ingest queue" and row[1] == ANSWER
            assert row[2] == ["kb.search"]
            assert row[3] == ["runbook.md", "postmortem-42"]
            assert row[4] == "g-done" and row[5] == 1
            assert [tuple(a) for a in audit] == [("golden_task_promoted", "g-done")]

            store = EvalSuiteStore(app_db, owner)
            run_id = uuid.uuid4().hex
            assert await store.start_run(sid, run_id, dataset_version=1, enqueue=True) == 1
            ctx = TenantContext(tenant_id=owner, plan=PlanTier.PROFESSIONAL, api_key_id="k")
            rerun = _Rerun()
            out = await run_until_done(
                store=store, run_id=run_id, goal_service=rerun, tenant_ctx=ctx,
                cfg=RunSettings(FastSettings()),
            )
            assert out["status"] == "completed", out
            assert [s["goal"] for s in rerun.submits] == ["Fix the stuck ingest queue"]
            tasks = await store.list_run_tasks(run_id)
            assert [(t["task_id"], t["passed"]) for t in tasks] == [("goal-g-done", True)]
        finally:
            await app_engine.dispose()
            await admin_engine.dispose()
