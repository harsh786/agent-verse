"""CORE-18 on real Postgres: a distributed strategy run resumes after a crash.

A supervisor run decomposes, runs its sub-tasks, then the worker "dies" during
synthesis. The redelivered run (a fresh executor, as a restarted worker builds)
resumes from strategy_run_checkpoints: no second decomposition, no sub-task
re-run, and synthesis uses the sub-task answers stored with the checkpoint.
A voyager run that dies mid-curriculum resumes the same way: same curriculum,
no finished task re-run, one skill published.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import ExecutionTerminalState
from app.orchestration.strategy_executor import (
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext
from tests.orchestration.test_distributed_hitl_gates import _limits, _request

_PLAN = '{"steps": [{"id": "s1", "summary": "collect facts"}, {"id": "s2", "summary": "rank them"}]}'


class _Provider:
    def __init__(self, *, crash_on_synthesis: bool) -> None:
        self.crash = crash_on_synthesis
        self.prompts: list[str] = []
        self._default_model = "m"

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = request.messages[0].content
        self.prompts.append(prompt)
        if prompt.startswith("Break the following goal"):
            content = _PLAN
        elif prompt.startswith("Combine these sub-task results"):
            if self.crash:
                raise ConnectionError("worker lost mid-synthesis")
            content = "final: " + prompt.split(":", 1)[1].strip()[:200]
        else:
            content = "answer for " + prompt.rsplit("Sub-task:", 1)[-1].strip()
        return CompletionResponse(content=content, model="m")


class _VoyagerProvider:
    def __init__(self, *, crash_on_second_task: bool) -> None:
        self.crash = crash_on_second_task
        self.prompts: list[str] = []
        self._default_model = "m"

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = request.messages[0].content
        self.prompts.append(prompt)
        if prompt.startswith("List 1-"):
            content = _PLAN
        elif prompt.startswith("Combine these task results"):
            content = "final: " + prompt.split(":", 1)[1].strip()[:200]
        elif self.crash and prompt.endswith("Task: rank them"):
            raise ConnectionError("worker lost mid-task")
        else:
            content = "result of " + prompt.rsplit("Task:", 1)[-1].strip()
        return CompletionResponse(content=content, model="m")


async def _run(factory: Any, tenant_ctx: TenantContext, goal_id: str, provider: Any,
               *, strategy: str = "supervisor", library: Any = None) -> Any:
    store = StrategyGoalContextStore()
    base = _request(strategy).model_dump()
    await store.put(
        base["context_snapshot_ref"],
        StrategyGoalContext(goal_text="Compare three databases", provider=provider,
                            tenant_ctx=tenant_ctx),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(
            context_store=store, checkpoint_db=lambda: factory, skill_store=library
        ),
        admission=default_distributed_admission,
    )
    request = type(_request(strategy)).model_validate(
        {**base, "tenant_id": tenant_ctx.tenant_id, "goal_id": goal_id}
    )
    return await runner.run(request, _limits())


@pytest.mark.integration
async def test_redelivered_supervisor_run_resumes_from_the_stored_checkpoint(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, goal_id = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    async def _exec(sql: str, params: dict[str, Any]) -> list[Any]:
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            res = await s.execute(text(sql), params)
            return list(res.all()) if res.returns_rows else []

    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'sc', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec("INSERT INTO goals (id, tenant_id, goal_text, status) "
                    "VALUES (:i, :t, 'Compare three databases', 'executing')",
                    {"i": goal_id, "t": tenant_id})

        crashed = _Provider(crash_on_synthesis=True)
        first = await _run(factory, ctx, goal_id, crashed)
        assert first.terminal_state is not ExecutionTerminalState.SUCCEEDED
        assert sum(p.startswith("Complete this sub-task") for p in crashed.prompts) == 2

        resumed = _Provider(crash_on_synthesis=False)
        second = await _run(factory, ctx, goal_id, resumed)
        assert second.terminal_state is ExecutionTerminalState.SUCCEEDED, (
            second.safe_rationale_summary, second.trace_summary, resumed.prompts
        )
        assert not any(p.startswith("Break the following goal") for p in resumed.prompts)
        assert not any(p.startswith("Complete this sub-task") for p in resumed.prompts)
        [synthesis] = [p for p in resumed.prompts if p.startswith("Combine")]
        assert "answer for collect facts" in synthesis and "answer for rank them" in synthesis

        rows = await _exec(
            "SELECT state_type, state ->> 'phase' FROM strategy_run_checkpoints "
            "WHERE tenant_id = :t AND goal_id = :g",
            {"t": tenant_id, "g": goal_id},
        )
        assert rows == [("DurablePatternState", "completed")]
    finally:
        await _exec("DELETE FROM goals WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()


@pytest.mark.integration
async def test_redelivered_voyager_run_resumes_on_the_same_curriculum(pg_url: str) -> None:
    from tests.orchestration.test_voyager_strategy import _Library

    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, goal_id = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    async def _exec(sql: str, params: dict[str, Any]) -> list[Any]:
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            res = await s.execute(text(sql), params)
            return list(res.all()) if res.returns_rows else []

    library = _Library()
    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'sc', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec("INSERT INTO goals (id, tenant_id, goal_text, status) "
                    "VALUES (:i, :t, 'Compare three databases', 'executing')",
                    {"i": goal_id, "t": tenant_id})

        crashed = _VoyagerProvider(crash_on_second_task=True)
        first = await _run(factory, ctx, goal_id, crashed, strategy="voyager", library=library)
        assert first.terminal_state is not ExecutionTerminalState.SUCCEEDED
        assert library.published == []

        resumed = _VoyagerProvider(crash_on_second_task=False)
        second = await _run(factory, ctx, goal_id, resumed, strategy="voyager", library=library)
        assert second.terminal_state is ExecutionTerminalState.SUCCEEDED, (
            second.safe_rationale_summary, resumed.prompts
        )
        assert not any(p.startswith("List 1-") for p in resumed.prompts)
        assert not any(p.endswith("Task: collect facts") for p in resumed.prompts)
        assert len(library.published) == 1
        [final] = [p for p in resumed.prompts if p.startswith("Combine")]
        assert "result of collect facts" in final and "result of rank them" in final

        rows = await _exec(
            "SELECT state_type, state ->> 'phase' FROM strategy_run_checkpoints "
            "WHERE tenant_id = :t AND goal_id = :g",
            {"t": tenant_id, "g": goal_id},
        )
        assert rows == [("VoyagerState", "completed")]
    finally:
        await _exec("DELETE FROM goals WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()
