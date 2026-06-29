"""Tests for A2A internal dispatch for civilization members."""
from __future__ import annotations

import hashlib
import hmac as _hmac
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.civilization.a2a_dispatch import _sign_payload, dispatch_internal_task


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_tenant_ctx() -> Any:
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


# ── _sign_payload ─────────────────────────────────────────────────────────────


def test_sign_payload_has_sha256_prefix():
    sig = _sign_payload(b"hello world", "my-secret")
    assert sig.startswith("sha256=")


def test_sign_payload_produces_deterministic_result():
    payload = b'{"goal": "test task"}'
    secret = "shared-secret"
    sig1 = _sign_payload(payload, secret)
    sig2 = _sign_payload(payload, secret)
    assert sig1 == sig2


def test_sign_payload_matches_hmac_sha256():
    payload = b"test payload data"
    secret = "my-signing-key"
    sig = _sign_payload(payload, secret)
    expected_hex = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected_hex}"


def test_sign_payload_different_secrets_differ():
    payload = b"same data"
    sig1 = _sign_payload(payload, "secret-a")
    sig2 = _sign_payload(payload, "secret-b")
    assert sig1 != sig2


def test_sign_payload_different_payloads_differ():
    secret = "same-secret"
    sig1 = _sign_payload(b"payload-a", secret)
    sig2 = _sign_payload(b"payload-b", secret)
    assert sig1 != sig2


def test_sign_payload_empty_payload():
    sig = _sign_payload(b"", "secret")
    assert sig.startswith("sha256=")


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
