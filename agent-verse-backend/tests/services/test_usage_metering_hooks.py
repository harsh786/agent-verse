"""Usage metering is recorded for goal completions and tool calls (API + worker).

Regression: nothing called UsageService.record / record_goal_completion /
record_tool_call, so GET /billing/usage was always empty.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.services.usage_service import UsageService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-usage", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _svc_with_usage() -> tuple[GoalService, UsageService]:
    usage = UsageService()
    svc = GoalService()
    svc._app_state = types.SimpleNamespace(usage_service=usage)
    svc._goals["g-u"] = GoalRecord(
        goal_id="g-u", goal_text="t", status=GoalStatus.EXECUTING, tenant_id=CTX.tenant_id,
        priority="normal", dry_run=False, created_at="2026-01-01T00:00:00",
    )
    return svc, usage


def _metrics(usage: UsageService) -> list[str]:
    return [r["metric"] for r in usage._buffer]


@pytest.mark.asyncio
async def test_api_path_records_tool_calls_and_one_goal_completion() -> None:
    svc, usage = _svc_with_usage()
    with patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()):
        await svc._dispatch_event(
            "g-u",
            {"type": "tool_call_complete", "tool": "jira_search", "server_id": "jira",
             "success": True},
            tenant_ctx=CTX,
        )
        await svc._dispatch_event("g-u", {"type": "goal_complete"}, tenant_ctx=CTX)
        await svc._dispatch_event("g-u", {"type": "goal_complete"}, tenant_ctx=CTX)

    assert _metrics(usage).count("tool_calls") == 1
    assert _metrics(usage).count("goals") == 1, "a goal is metered once"
    tool = next(r for r in usage._buffer if r["metric"] == "tool_calls")
    assert tool["goal_id"] == "g-u" and tool["metadata"]["tool"] == "jira_search"
    summary = await usage.get_usage_summary(CTX.tenant_id)
    assert summary["usage"]["goals"] == 1.0


@pytest.mark.asyncio
async def test_api_path_does_not_meter_dry_runs() -> None:
    svc, usage = _svc_with_usage()
    svc._goals["g-u"].dry_run = True
    with patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()):
        await svc._dispatch_event("g-u", {"type": "goal_complete"}, tenant_ctx=CTX)
    assert usage._buffer == []


@pytest.mark.asyncio
async def test_metering_failure_is_logged_not_raised() -> None:
    from app.services import usage_metering

    broken = AsyncMock()
    broken.record_tool_call.side_effect = RuntimeError("db down")
    with patch.object(usage_metering.logger, "warning") as warn:
        await usage_metering.meter_tool_call(
            broken, tenant_id="t", goal_id="g", event={"type": "tool_call_complete"}
        )
    warn.assert_called_once()
    assert warn.call_args.args[0] == "usage_tool_call_record_failed"


@pytest.mark.asyncio
async def test_goal_completion_uses_the_cost_breakdown_tokens() -> None:
    from app.observability import cost_breakdown as cb
    from app.services import usage_metering

    cb.reset_db()
    cb.record_role_cost("g-tok", "planner", "m", 100, 20, 0.5)
    usage = UsageService()
    await usage_metering.meter_goal_completion(
        usage, tenant_id="t", goal_id="g-tok", status="complete"
    )
    tokens = next(r for r in usage._buffer if r["metric"] == "llm_tokens")
    assert tokens["quantity"] == 120.0
    summary = await usage.get_usage_summary("t")
    assert summary["total_cost_usd"] == pytest.approx(0.5), "goal cost is not double counted"


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 1


def test_worker_path_records_tool_calls_and_goal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    usage = UsageService()
    monkeypatch.setattr(tasks, "_worker_usage_service", lambda: usage)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> _State:
            await kwargs["event_callback"](
                {"type": "tool_call_complete", "tool": "gh_pr", "server_id": "github",
                 "success": True}
            )
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    tasks.run_goal.run("goal-usage-w", "tenant-usage", "g", "normal", False)

    metrics = [r["metric"] for r in usage._buffer]
    assert metrics.count("tool_calls") == 1
    assert metrics.count("goals") == 1
    assert all(r["tenant_id"] == "tenant-usage" for r in usage._buffer)
