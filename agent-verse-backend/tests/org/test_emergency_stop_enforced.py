"""Org emergency stop must actually stop the org's goals.

``POST /orgs/{org}/emergency-stop`` writes ``emergency_stop:{tenant}:{org}``,
but the only reader (the Celery ``run_goal`` task) checked
``emergency_stop:{tenant}`` — so the endpoint said "stopped" while the org's
goals kept running. In-process (API) execution checked no flag at all.
"""
from __future__ import annotations

import fnmatch
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.emergency_stop import (
    ORG_STOP_REASON,
    ORG_UNVERIFIED_REASON,
    TENANT_STOP_REASON,
    emergency_stop_reason,
    emergency_stop_reason_sync,
)
from app.tenancy.context import PlanTier, TenantContext

TID = "t1"


class _SyncRedis:
    def __init__(self, keys: dict[str, str]) -> None:
        self.keys = keys

    def get(self, k: str) -> str | None:
        return self.keys.get(k)

    def scan_iter(self, match: str, count: int = 100) -> Any:
        return iter([k.encode() for k in self.keys if fnmatch.fnmatchcase(k, match)])


class _AsyncRedis:
    def __init__(self, keys: dict[str, str]) -> None:
        self.keys = keys

    async def get(self, k: str) -> str | None:
        return self.keys.get(k)


# ── shared reader ────────────────────────────────────────────────────────────


def test_org_stop_blocks_goal_of_that_org() -> None:
    r = _SyncRedis({f"emergency_stop:{TID}:org-a": "1"})
    assert emergency_stop_reason_sync(r, TID, resolve_org_id=lambda: "org-a") == ORG_STOP_REASON


def test_org_stop_does_not_block_other_orgs_goal() -> None:
    r = _SyncRedis({f"emergency_stop:{TID}:org-a": "1"})
    assert emergency_stop_reason_sync(r, TID, resolve_org_id=lambda: "org-b") is None
    assert emergency_stop_reason_sync(r, TID, resolve_org_id=lambda: None) is None


def test_tenant_stop_blocks_everything() -> None:
    r = _SyncRedis({f"emergency_stop:{TID}": "1"})
    assert emergency_stop_reason_sync(r, TID) == TENANT_STOP_REASON


def test_no_org_stop_skips_org_lookup() -> None:
    resolver = MagicMock(return_value="org-a")
    assert emergency_stop_reason_sync(_SyncRedis({}), TID, resolve_org_id=resolver) is None
    resolver.assert_not_called()


def test_org_lookup_failure_fails_closed_while_org_stop_active() -> None:
    r = _SyncRedis({f"emergency_stop:{TID}:org-a": "1"})

    def boom() -> str:
        raise RuntimeError("db down")

    assert emergency_stop_reason_sync(r, TID, resolve_org_id=boom) == ORG_UNVERIFIED_REASON


def test_other_tenants_org_stop_is_ignored() -> None:
    r = _SyncRedis({"emergency_stop:t2:org-a": "1"})
    assert emergency_stop_reason_sync(r, TID, resolve_org_id=lambda: "org-a") is None


@pytest.mark.asyncio
async def test_async_reader_honours_org_and_tenant_keys() -> None:
    assert (
        await emergency_stop_reason(_AsyncRedis({f"emergency_stop:{TID}:o": "1"}), TID, "o")
        == ORG_STOP_REASON
    )
    assert await emergency_stop_reason(_AsyncRedis({f"emergency_stop:{TID}": "1"}), TID, None)
    assert await emergency_stop_reason(_AsyncRedis({}), TID, "o") is None


# ── Celery worker reader (app/scaling/tasks.py run_goal) ─────────────────────


def test_worker_run_goal_blocked_by_org_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from app.scaling.tasks import run_goal

    r = _SyncRedis({f"emergency_stop:{TID}:org-a": "1"})
    with (
        patch("app.scaling.tasks._get_sync_redis", return_value=r),
        patch("app.governance.emergency_stop.goal_org_id", AsyncMock(return_value="org-a")),
    ):
        result = run_goal.run(goal_id="g-org", tenant_id=TID, goal_text="x", dry_run=False)

    assert result["status"] == "blocked"
    assert result["reason"] == ORG_STOP_REASON


# ── In-process (API) execution: AgentGraph.run ───────────────────────────────


@pytest.mark.asyncio
async def test_agent_graph_refuses_to_run_for_stopped_org() -> None:
    from fastapi import FastAPI

    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider

    p = FakeProvider(responses=['{"steps": ["s"]}', "done", '{"success": true, "reason": "ok"}'])
    g = AgentGraph(planner=p, executor=p, verifier=p)
    app = FastAPI()
    app.state._redis = _AsyncRedis({f"emergency_stop:{TID}:org-a": "1"})
    g._app_state = app
    g._graph = MagicMock()
    g._graph.ainvoke = AsyncMock()

    ctx = TenantContext(tenant_id=TID, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    state = await g.run(goal="x", tenant_ctx=ctx, initial_context={"org_id": "org-a"})

    assert state.status == GoalStatus.FAILED
    assert ORG_STOP_REASON in state.error_message
    g._graph.ainvoke.assert_not_awaited()


def test_a_goal_blocked_by_an_emergency_stop_is_recorded_and_frees_its_slot(monkeypatch) -> None:
    """Regression: a blocked goal returned {"status": "blocked"} but its row stayed
    queued forever and its concurrency slot was never released."""
    from unittest.mock import AsyncMock, MagicMock

    import app.scaling.tasks as tasks

    fake_redis = MagicMock()
    fake_redis.get = MagicMock(side_effect=lambda k: b"1" if k == "emergency_stop:t-es" else None)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: fake_redis)
    marked = AsyncMock()
    freed = AsyncMock()
    monkeypatch.setattr(tasks, "_mark_goal_blocked", marked)
    monkeypatch.setattr(tasks, "_decrement_after_completion", freed)
    result = tasks.run_goal.run(goal_id="g-es", tenant_id="t-es", goal_text="x", plan="free")
    assert result["status"] == "blocked"
    marked.assert_awaited_once()
    freed.assert_awaited_once()
