"""Tests for A2A internal dispatch for civilization members."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.civilization.a2a_dispatch import dispatch_internal_task

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_tenant_ctx() -> Any:
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


# ── dispatch_internal_task ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_without_goal_service_returns_accepted():
    """No goal_service → accepted with None goal_id (no submission attempted)."""
    result = await dispatch_internal_task(
        from_agent_id="agent-a",
        to_agent_id="agent-b",
        goal="Analyze performance metrics",
        context={"priority": "high"},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=None,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert result["status"] == "accepted"
    assert result["goal_id"] is None
    assert result["from_agent_id"] == "agent-a"
    assert result["to_agent_id"] == "agent-b"
    assert result["civilization_id"] == "civ-1"
    assert "task_id" in result
    assert "message" in result


@pytest.mark.asyncio
async def test_dispatch_with_goal_service_submits_goal():
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(return_value={"goal_id": "goal-xyz"})

    result = await dispatch_internal_task(
        from_agent_id="agent-a",
        to_agent_id="agent-b",
        goal="Build a Jira report",
        context={"parent_goal_id": "g0"},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert result["status"] == "accepted"
    assert result["goal_id"] == "goal-xyz"
    mock_gs.submit_goal.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_goal_service_passes_a2a_context():
    """Execution context must include a2a_task_id and from_agent_id."""
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(return_value={"goal_id": "g2"})

    await dispatch_internal_task(
        from_agent_id="sender-agent",
        to_agent_id="receiver-agent",
        goal="Summarize logs",
        context={"extra_key": "extra_val"},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
    )
    call_kwargs = mock_gs.submit_goal.call_args.kwargs
    exec_ctx = call_kwargs["execution_context"]
    assert "a2a_task_id" in exec_ctx
    assert exec_ctx["from_agent_id"] == "sender-agent"
    assert exec_ctx["civilization_id"] == "civ-1"
    assert exec_ctx["extra_key"] == "extra_val"


@pytest.mark.asyncio
async def test_dispatch_goal_service_passes_correct_agent_and_priority():
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(return_value={"goal_id": "g3"})

    await dispatch_internal_task(
        from_agent_id="a1",
        to_agent_id="target-agent",
        goal="Do urgent task",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
        priority="high",
    )
    call_kwargs = mock_gs.submit_goal.call_args.kwargs
    assert call_kwargs["agent_id"] == "target-agent"
    assert call_kwargs["priority"] == "high"


@pytest.mark.asyncio
async def test_dispatch_goal_service_exception_returns_failed():
    """If goal_service raises, returns a 'failed' status dict."""
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(side_effect=RuntimeError("GoalService unavailable"))

    result = await dispatch_internal_task(
        from_agent_id="agent-a",
        to_agent_id="agent-b",
        goal="Some goal",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert result["status"] == "failed"
    assert "GoalService unavailable" in result["error"]
    assert result["task_id"]
    assert result["from_agent_id"] == "agent-a"
    assert result["to_agent_id"] == "agent-b"
    assert result["civilization_id"] == "civ-1"


@pytest.mark.asyncio
async def test_dispatch_returns_unique_task_ids():
    """Each dispatch call produces a unique task_id."""
    ids = set()
    for _ in range(5):
        result = await dispatch_internal_task(
            from_agent_id="a",
            to_agent_id="b",
            goal="task",
            context={},
            civilization_id="civ-1",
            tenant_id="t1",
            goal_service=None,
            tenant_ctx=_make_tenant_ctx(),
        )
        ids.add(result["task_id"])
    assert len(ids) == 5


@pytest.mark.asyncio
async def test_dispatch_message_contains_to_agent_id():
    result = await dispatch_internal_task(
        from_agent_id="a",
        to_agent_id="my-special-agent",
        goal="do something",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=None,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert "my-special-agent" in result["message"]


@pytest.mark.asyncio
async def test_dispatch_callback_url_parameter_accepted():
    """callback_url is accepted but not used in current implementation."""
    result = await dispatch_internal_task(
        from_agent_id="a",
        to_agent_id="b",
        goal="task",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=None,
        tenant_ctx=_make_tenant_ctx(),
        callback_url="https://my.callback.url/hook",
    )
    assert result["status"] == "accepted"


# ── durable-intent repository (idempotency) ──────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_with_repository():
    """Two dispatches sharing an idempotency_key submit the goal exactly once."""
    from app.civilization.a2a_repository import InMemoryA2ARepository

    repo = InMemoryA2ARepository()
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(return_value={"goal_id": "goal-1"})

    kwargs = dict(
        from_agent_id="agent-a",
        to_agent_id="agent-b",
        goal="Analyze metrics",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
        repository=repo,
        idempotency_key="same-key",
    )

    first = await dispatch_internal_task(**kwargs)  # type: ignore[arg-type]
    second = await dispatch_internal_task(**kwargs)  # type: ignore[arg-type]

    assert first["status"] == "accepted"
    assert first["goal_id"] == "goal-1"
    # Second call short-circuits on the already-accepted record.
    assert second["task_id"] == first["task_id"]
    assert second["status"] == "accepted"
    assert "already accepted" in second["message"]
    # The goal was submitted only ONCE despite two dispatch calls.
    mock_gs.submit_goal.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_same_key_yields_same_task_id():
    from app.civilization.a2a_repository import InMemoryA2ARepository

    repo = InMemoryA2ARepository()
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(return_value={"goal_id": "g"})

    common = dict(
        from_agent_id="a",
        to_agent_id="b",
        goal="task",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
        repository=repo,
        idempotency_key="k-123",
    )
    r1 = await dispatch_internal_task(**common)  # type: ignore[arg-type]
    r2 = await dispatch_internal_task(**common)  # type: ignore[arg-type]
    assert r1["task_id"] == r2["task_id"]


@pytest.mark.asyncio
async def test_dispatch_records_failure_in_repository():
    """On goal_service failure the durable record is transitioned to 'failed'."""
    from app.civilization.a2a_repository import InMemoryA2ARepository

    repo = InMemoryA2ARepository()
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(side_effect=RuntimeError("boom"))

    result = await dispatch_internal_task(
        from_agent_id="a",
        to_agent_id="b",
        goal="task",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
        repository=repo,
        idempotency_key="fail-key",
    )
    assert result["status"] == "failed"
    record = await repo.get("t1", result["task_id"])
    assert record is not None
    assert record.status == "failed"


# ── membership guard ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_rejects_inactive_membership():
    """Both agents must be active members of the same civilization."""
    membership = AsyncMock()
    membership.active_member = AsyncMock(side_effect=[True, False])  # source active, target not

    with pytest.raises(PermissionError):
        await dispatch_internal_task(
            from_agent_id="a",
            to_agent_id="b",
            goal="task",
            context={},
            civilization_id="civ-1",
            tenant_id="t1",
            goal_service=AsyncMock(),
            tenant_ctx=_make_tenant_ctx(),
            membership=membership,
        )


@pytest.mark.asyncio
async def test_dispatch_allows_active_membership():
    membership = AsyncMock()
    membership.active_member = AsyncMock(return_value=True)
    mock_gs = AsyncMock()
    mock_gs.submit_goal = AsyncMock(return_value={"goal_id": "g"})

    result = await dispatch_internal_task(
        from_agent_id="a",
        to_agent_id="b",
        goal="task",
        context={},
        civilization_id="civ-1",
        tenant_id="t1",
        goal_service=mock_gs,
        tenant_ctx=_make_tenant_ctx(),
        membership=membership,
    )
    assert result["status"] == "accepted"
