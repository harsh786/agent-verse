"""The re-validation state machine (owner decision on a05-F095-04), unit level.

In-memory AgentStore + EvalSuiteStore: the post-run resolution promotes only
the agent's CURRENT pending marker (compare-and-set on mode + token), a failed
run or failed gate leaves it bounded with the reason, and a dispatch failure
turns the marker failed instead of leaving it pending forever.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.api.agents import AgentStore
from app.intelligence import autonomy_revalidation as reval
from app.intelligence.eval_suite import EvalSuiteResult, GoldenTaskResult
from app.intelligence.eval_suite_post_run import on_run_completed
from app.intelligence.eval_suite_store import EvalSuiteStore
from app.tenancy.context import PlanTier, TenantContext


class _Audit:
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    async def record_async(self, event: Any, *, tenant_ctx: Any) -> None:
        self.outcomes.append(event.outcome)


async def _setup(
    *, passed: int = 5
) -> tuple[AgentStore, EvalSuiteStore, TenantContext, str, dict[str, Any]]:
    """A demoted agent with a pending marker and its finished re-validation run."""
    ctx = TenantContext(tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE,
                        api_key_id="k")
    agents = AgentStore()
    suite = f"s-{uuid.uuid4().hex[:6]}"
    agent_id = await agents.create(
        {"name": "a", "autonomy_mode": "fully-autonomous", "eval_suite_id": suite,
         "system_prompt": "v1"},
        tenant_ctx=ctx,
    )
    evals = EvalSuiteStore(None, ctx.tenant_id)
    await evals.create(suite, name=suite, description="")
    await evals.import_tasks(
        suite, [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(5)], replace=False
    )
    current = await agents.get_async(agent_id, tenant_ctx=ctx)
    assert current is not None
    proposed = {**current, "system_prompt": "v2", "autonomy_mode": "bounded-autonomous"}
    marker = await reval.begin_revalidation(
        evals, agent_id=agent_id, proposed=proposed, source="agent_update", actor="k",
        tenant_plan="free",
    )
    assert marker["state"] == "pending"
    await agents.update_async(
        agent_id,
        {"system_prompt": "v2", "autonomy_mode": "bounded-autonomous",
         "autonomy_revalidation": marker},
        tenant_ctx=ctx,
    )
    await evals.finish_run(suite, marker["run_id"], result=EvalSuiteResult(
        suite_id=suite, run_id=marker["run_id"], total_tasks=5, passed_tasks=passed,
        failed_tasks=5 - passed,
        task_results=[GoldenTaskResult(task_id=f"t{i}", goal="g", passed=i < passed)
                      for i in range(5)],
    ))
    run = await evals.get_run(marker["run_id"])
    assert run is not None
    return agents, evals, ctx, agent_id, run


async def _agent(agents: AgentStore, agent_id: str, ctx: TenantContext) -> dict[str, Any]:
    rec = await agents.get_async(agent_id, tenant_ctx=ctx)
    assert rec is not None
    return rec


async def test_a_passing_run_promotes_and_is_idempotent() -> None:
    agents, evals, ctx, agent_id, run = await _setup(passed=5)
    audit = _Audit()
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx, run=run, audit_log=audit
    ) == "promoted"
    rec = await _agent(agents, agent_id, ctx)
    assert rec["autonomy_mode"] == "fully-autonomous"
    assert rec["autonomy_revalidation"]["state"] == "promoted"
    assert rec["autonomy_revalidation"]["pass_rate"] == 1.0
    assert rec["pending_promotion"] is False
    assert audit.outcomes == ["promoted"]
    # A duplicate hook / the reconciler finds nothing left to do.
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx, run=run, audit_log=audit
    ) is None
    assert audit.outcomes == ["promoted"]


async def test_a_run_below_the_threshold_leaves_the_agent_bounded() -> None:
    agents, evals, ctx, agent_id, run = await _setup(passed=2)
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx, run=run
    ) == "failed"
    rec = await _agent(agents, agent_id, ctx)
    assert rec["autonomy_mode"] == "bounded-autonomous"
    assert rec["autonomy_revalidation"]["state"] == "failed"
    assert rec["autonomy_revalidation"]["pass_rate"] == 0.4
    assert "below" in rec["autonomy_revalidation"]["error"]


async def test_a_failed_run_resolves_the_marker_failed() -> None:
    agents, evals, ctx, agent_id, run = await _setup(passed=5)
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx,
        run={**run, "status": "failed", "error": "suite deleted"},
    ) == "failed"
    rec = await _agent(agents, agent_id, ctx)
    assert rec["autonomy_mode"] == "bounded-autonomous"
    assert "suite deleted" in rec["autonomy_revalidation"]["error"]


async def test_an_operator_autonomy_change_is_never_overridden() -> None:
    agents, evals, ctx, agent_id, run = await _setup(passed=5)
    # The operator moved it to supervised (marker left as it was, worst case).
    await agents.update_async(agent_id, {"autonomy_mode": "supervised"}, tenant_ctx=ctx)
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx, run=run
    ) is None
    assert (await _agent(agents, agent_id, ctx))["autonomy_mode"] == "supervised"


async def test_an_operator_who_demotes_and_re_promotes_still_cancels_the_marker() -> None:
    """Even back at bounded, a token that changed (cancelled marker) never promotes."""
    agents, evals, ctx, agent_id, run = await _setup(passed=5)
    rec = await _agent(agents, agent_id, ctx)
    cancelled = reval.cancelled_by_operator(
        rec["autonomy_revalidation"], new_mode="bounded-autonomous", actor="op"
    )
    await agents.update_async(agent_id, {"autonomy_revalidation": cancelled}, tenant_ctx=ctx)
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx, run=run
    ) is None
    assert (await _agent(agents, agent_id, ctx))["autonomy_mode"] == "bounded-autonomous"


async def test_a_superseded_run_never_promotes() -> None:
    agents, evals, ctx, agent_id, run = await _setup(passed=5)
    rec = await _agent(agents, agent_id, ctx)
    newer = {**rec["autonomy_revalidation"], "token": uuid.uuid4().hex,
             "run_id": uuid.uuid4().hex}
    await agents.update_async(agent_id, {"autonomy_revalidation": newer}, tenant_ctx=ctx)
    assert await reval.resolve_revalidation(
        agent_store=agents, eval_store=evals, tenant_ctx=ctx, run=run
    ) is None
    rec = await _agent(agents, agent_id, ctx)
    assert rec["autonomy_mode"] == "bounded-autonomous"
    assert rec["autonomy_revalidation"]["state"] == "pending"


async def test_the_compare_and_set_refuses_a_stale_token() -> None:
    agents, _evals, ctx, agent_id, _run = await _setup()
    assert not await agents.transition_revalidation(
        agent_id, token="not-the-token", data={"autonomy_mode": "fully-autonomous"},
        tenant_ctx=ctx,
    )
    assert (await _agent(agents, agent_id, ctx))["autonomy_mode"] == "bounded-autonomous"


async def test_the_post_run_hook_resolves_through_the_given_agent_store() -> None:
    agents, evals, ctx, agent_id, run = await _setup(passed=5)
    await on_run_completed(evals, run, ctx, agent_store=agents)
    assert (await _agent(agents, agent_id, ctx))["autonomy_mode"] == "fully-autonomous"


async def test_a_dispatch_failure_marks_the_marker_failed_and_fails_the_run() -> None:
    ctx = TenantContext(tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE,
                        api_key_id="k")
    agents = AgentStore()
    suite = f"s-{uuid.uuid4().hex[:6]}"
    agent_id = await agents.create(
        {"name": "a", "autonomy_mode": "fully-autonomous", "eval_suite_id": suite},
        tenant_ctx=ctx,
    )
    evals = EvalSuiteStore(None, ctx.tenant_id)
    await evals.create(suite, name=suite, description="")
    await evals.import_tasks(suite, [{"goal": "g", "expected_tools": ["t"]}], replace=False)
    current = await _agent(agents, agent_id, ctx)
    marker = await reval.begin_revalidation(
        evals, agent_id=agent_id, proposed={**current, "system_prompt": "v2"},
        source="agent_update", actor="k", tenant_plan="free",
    )
    await agents.update_async(
        agent_id, {"autonomy_mode": "bounded-autonomous", "autonomy_revalidation": marker},
        tenant_ctx=ctx,
    )

    async def _broken(_t: str, _p: str, _r: str) -> None:
        raise RuntimeError("broker down")

    audit = _Audit()
    out = await reval.start_revalidation(
        eval_store=evals, agent_store=agents, tenant_ctx=ctx, agent_id=agent_id,
        marker=marker, dispatch=_broken, tenant_plan="free", audit_log=audit,
    )
    assert out["state"] == "failed" and "broker down" in out["error"]
    rec = await _agent(agents, agent_id, ctx)
    assert rec["autonomy_revalidation"]["state"] == "failed"
    assert rec["autonomy_mode"] == "bounded-autonomous"
    run = await evals.get_run(marker["run_id"])
    assert run is not None and run["status"] == "failed"
    assert audit.outcomes == ["demoted", "revalidation_failed"]


async def test_an_empty_suite_cannot_start_and_the_marker_says_so() -> None:
    ctx = TenantContext(tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE,
                        api_key_id="k")
    evals = EvalSuiteStore(None, ctx.tenant_id)
    await evals.create("empty", name="empty", description="")
    marker = await reval.begin_revalidation(
        evals, agent_id="a", proposed={"eval_suite_id": "empty"}, source="agent_update",
        actor=None, tenant_plan="free",
    )
    assert marker["state"] == "failed" and "no golden tasks" in marker["error"]
    assert marker["run_id"] is None
