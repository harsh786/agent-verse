"""MEM-53 against real Postgres (NOBYPASSRLS app role): durable suite runs.

* 2000 golden tasks are enqueued with one INSERT ... SELECT and executed by two
  concurrent step chains: every task runs exactly once, the run finalizes once;
* a step that died after recording a goal is resumed by polling that goal (no
  resubmission); one that died before submitting is re-claimed after its lease;
* the stalled-run sweeper (maintenance session) re-dispatches a run without a
  heartbeat and does not touch a live one;
* per-task rows are invisible to another tenant.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import patch

import pytest

from tests.intelligence._eval_fakes import FakeGoals, FastSettings
from tests.intelligence._eval_pg import eval_postgres, provision_app_role

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_durable_runs_on_postgres(monkeypatch: Any) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.intelligence.eval_suite_jobs import RunSettings, run_until_done
    from app.intelligence.eval_suite_store import EvalSuiteStore
    from app.tenancy.context import PlanTier, TenantContext

    with eval_postgres() as admin_url:
        app_url = await provision_app_role(admin_url)
        admin_engine = create_async_engine(admin_url)
        app_engine = create_async_engine(app_url, pool_size=10)
        admin = async_sessionmaker(admin_engine, expire_on_commit=False)
        app = async_sessionmaker(app_engine, expire_on_commit=False)
        try:
            tenant = f"t-{uuid.uuid4().hex[:8]}"
            ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")
            store = EvalSuiteStore(app, tenant)
            await store.create("big", name="big", description="")
            await store.import_tasks(
                "big", [{"goal": f"goal {i}", "expected_tools": ["t"]} for i in range(2000)],
                replace=False,
            )
            run_id = uuid.uuid4().hex
            total = await store.start_run("big", run_id, dataset_version=1, enqueue=True,
                                          tenant_plan="professional", concurrency=50)
            assert total == 2000
            # One claim leases exactly one task. (As an UPDATE ... FROM (SELECT ...
            # LIMIT 1 FOR UPDATE SKIP LOCKED) the planner rescanned the subquery per
            # joined row and one claim leased dozens of tasks that never ran.)
            probe = await store.claim_pending(run_id, "probe", 30)
            assert probe is not None
            assert (await store.run_progress(run_id))["running"] == 1
            assert len(await store.claim_due(run_id, "probe", 30, 10)) == 0
            async with admin() as s, s.begin():
                await s.execute(
                    text("UPDATE eval_suite_task_results SET state = 'pending', attempts = 0, "
                         "lease_owner = NULL, lease_expires_at = NULL WHERE run_id = :r"),
                    {"r": run_id},
                )
            goals = FakeGoals(running_polls=1)
            cfg = RunSettings(FastSettings())
            completions: list[str] = []

            async def hook(_s: Any, run: dict[str, Any], _c: Any) -> None:
                completions.append(str(run["run_id"]))

            outs = await asyncio.gather(*(
                run_until_done(store=store, run_id=run_id, goal_service=goals, tenant_ctx=ctx,
                               on_completed=hook, cfg=cfg)
                for _ in range(2)
            ))
            assert {o["status"] for o in outs} == {"completed"}
            assert len(goals.submits) == 2000
            assert len({s["goal"] for s in goals.submits}) == 2000
            assert completions == [run_id]
            (run,) = await store.list_runs("big")
            assert run["status"] == "completed" and run["passed"] == 2000
            assert len(run["task_results"]) == 200  # summary on the row; the rest paged
            page = await store.list_run_tasks(run_id, limit=500, offset=1900)
            assert len(page) == 100

            # ── resume after a worker died ──────────────────────────────
            await store.import_tasks("big", [{"goal": "late 1", "expected_tools": ["t"]},
                                             {"goal": "late 2", "expected_tools": ["t"]}],
                                     replace=True)
            run2 = uuid.uuid4().hex
            assert await store.start_run("big", run2, dataset_version=2, enqueue=True,
                                         concurrency=2) == 2
            g2 = FakeGoals(running_polls=2)
            first = await store.claim_pending(run2, "dead-worker", 30)
            assert first is not None
            sub = await g2.submit_goal(goal=first["task"]["goal"], tenant_ctx=ctx)
            assert await store.mark_waiting(run2, first["task_id"], "dead-worker",
                                            sub["goal_id"], 600)
            second = await store.claim_pending(run2, "dead-worker", 30)
            assert second is not None
            async with admin() as s, s.begin():
                await s.execute(
                    text("UPDATE eval_suite_task_results SET lease_expires_at = now() "
                         "- interval '1 second' WHERE run_id = :r AND task_id = :t"),
                    {"r": run2, "t": second["task_id"]},
                )
                # ...and the run has had no heartbeat for a while.
                await s.execute(
                    text("UPDATE eval_suite_results SET last_progress_at = now() "
                         "- interval '1 hour' WHERE id = :r"), {"r": run2},
                )

            # The sweeper (maintenance session) re-dispatches exactly the stalled run.
            from app.scaling import tasks as scaling_tasks

            dispatched: list[list[Any]] = []

            class _Task:
                def apply_async(self, args: list[Any], **_: Any) -> None:
                    dispatched.append(args)

            monkeypatch.setattr(scaling_tasks, "run_eval_suite_worker", _Task())
            with patch("app.db.session.get_system_session_factory", return_value=admin):
                out = await scaling_tasks._resume_stalled_eval_suite_runs_async(60)
            assert out["resumed_runs"] == 1 and dispatched[0][2] == run2
            with patch("app.db.session.get_system_session_factory", return_value=admin):
                again = await scaling_tasks._resume_stalled_eval_suite_runs_async(60)
            assert again["resumed_runs"] == 0  # its heartbeat was bumped by the sweep

            done = await run_until_done(store=store, run_id=run2, goal_service=g2,
                                        tenant_ctx=ctx, cfg=cfg)
            assert done["status"] == "completed"
            assert len(g2.submits) == 2  # the recorded goal was polled, not resubmitted
            tasks = {t["task_id"]: t for t in await store.list_run_tasks(run2)}
            assert tasks[first["task_id"]]["goal_id"] == sub["goal_id"]
            assert tasks[second["task_id"]]["attempts"] == 2
            assert all(t["passed"] for t in tasks.values())

            # ── tenant isolation ────────────────────────────────────────
            other = EvalSuiteStore(app, f"o-{uuid.uuid4().hex[:8]}")
            assert await other.get_run(run_id) is None
            assert await other.list_run_tasks(run_id) == []
            assert await other.claim_pending(run_id, "x", 30) is None
            assert (await other.run_progress(run_id))["total"] == 0
        finally:
            await app_engine.dispose()
            await admin_engine.dispose()
