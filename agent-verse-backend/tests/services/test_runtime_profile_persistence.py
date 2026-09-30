"""The goal's runtime profile is serialized, persisted under RLS, and rollout-gated.

Regressions:
* ``profile_data`` carried the dataclass profile into ``json.dumps`` and the caller never
  passed a ``db_session`` — the profile columns were never written.
* the v2 rollout re-set ``profile_object`` that was already set, so shadow / allowlist /
  kill switch changed nothing about execution.
* a failed build was swallowed by ``except: pass`` with no trace on the goal.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import pytest

from app.core import runtime_flags
from app.orchestration.runtime_profile import GoalRuntimeProfile
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-p", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _flags(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    base = runtime_flags.get_runtime_flags()
    patched = replace(base, **overrides)
    monkeypatch.setattr(runtime_flags, "get_runtime_flags", lambda: patched)


async def test_profile_data_is_json_safe_and_legacy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, strategy_runtime_v2_tenant_allowlist=frozenset())
    data = await GoalService()._build_runtime_profile(
        "summarize the quarterly report", goal_id="g-1", tenant_ctx=TENANT
    )

    json.dumps(data["context"])
    json.dumps(data["columns"])
    assert data["profile_object"] is None
    assert data["context"]["strategy_runtime_path"] == "legacy"
    assert data["context"]["runtime_profile"]["goal_id"] == "g-1"
    columns = data["columns"]
    assert columns["runtime_profile_id"] == data["context"]["profile_id"]
    assert columns["runtime_profile_version"] == 2
    # Nothing ran yet: filled from strategy_execution later (CORE-21).
    assert columns["patterns_used"] == []
    assert columns["runtime_profile_snapshot"]["tenant_id"] == TENANT.tenant_id


async def test_allowlisted_tenant_executes_the_v2_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, strategy_runtime_v2_tenant_allowlist=frozenset({TENANT.tenant_id}))
    data = await GoalService()._build_runtime_profile("goal", goal_id="g-2", tenant_ctx=TENANT)
    assert isinstance(data["profile_object"], GoalRuntimeProfile)
    assert data["context"]["strategy_runtime_path"] == "v2"


@pytest.mark.parametrize(
    ("overrides", "path"),
    [
        ({"strategy_runtime_v2_shadow": True}, "legacy"),
        ({"strategy_runtime_v2_kill_switch": True}, "rejected"),
    ],
)
async def test_shadow_and_kill_switch_never_dispatch_v2(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], path: str
) -> None:
    _flags(
        monkeypatch,
        strategy_runtime_v2_tenant_allowlist=frozenset({TENANT.tenant_id}),
        **overrides,
    )
    data = await GoalService()._build_runtime_profile(
        "goal",
        goal_id="g-3",
        tenant_ctx=TENANT,
        agent_config={"primary_strategy": "plan_execute"},
    )
    assert data["profile_object"] is None
    assert data["context"]["strategy_runtime_path"] == path
    # The explicit request cannot run on the legacy path — the goal says so.
    fallback = data["context"]["runtime_profile_fallback"]
    assert fallback["requested_primary"] == "plan_execute"
    assert fallback["fallback"] == "legacy"


async def test_build_failure_is_logged_and_recorded_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(
        "app.orchestration.runtime_profile_builder.RuntimeProfileBuilder.build_with_trace", boom
    )
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "app.services.goal_service._svc_logger.warning",
        lambda event, **kw: events.append((event, kw)),
    )
    data = await GoalService()._build_runtime_profile("goal", goal_id="g-4", tenant_ctx=TENANT)

    assert data["profile_object"] is None
    fallback = data["context"]["runtime_profile_fallback"]
    assert fallback["reason"] == "profile_build_failed"
    assert fallback["error_type"] == "RuntimeError"
    assert events and events[0][0] == "runtime_profile_build_failed_legacy_fallback"


async def test_invalid_override_is_recorded_with_its_reason() -> None:
    data = await GoalService()._build_runtime_profile(
        "goal",
        goal_id="g-5",
        tenant_ctx=TENANT,
        agent_config={"primary_strategy": "rewoo"},
    )
    fallback = data["context"]["runtime_profile_fallback"]
    assert fallback["reason"] == "invalid_strategy_override"
    assert fallback["requested_primary"] == "rewoo"


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        self.statements.append((str(statement), dict(params or {})))
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


async def test_submission_persists_profile_columns_under_tenant_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _RecordingSession()

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    svc = GoalService(db_session_factory=factory)
    await svc.submit_goal(
        goal="summarize the quarterly report",
        priority="normal",
        dry_run=True,
        tenant_ctx=TENANT,
    )
    for task in list(svc._db_tasks):
        await task

    goals = [obj for obj in session.added if type(obj).__name__ == "Goal"]
    assert len(goals) == 1
    goal = goals[0]
    assert goal.runtime_profile_id
    assert goal.runtime_profile_version == 2
    assert goal.strategy_registry_revision.startswith("sha256:")
    assert goal.runtime_profile_snapshot["goal_id"] == goal.id
    assert goal.patterns_used == []  # a dry run executes nothing (CORE-21)
    assert goal.rag_strategy_used
    json.dumps(goal.execution_context)
    # The insert ran after the tenant's RLS GUC was set on the same session.
    assert any(
        "set_config('app.tenant_id'" in sql and params.get("tid") == TENANT.tenant_id
        for sql, params in session.statements
    )
