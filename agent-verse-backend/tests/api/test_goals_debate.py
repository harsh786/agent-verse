"""Tests for debate workflow_mode in goal submission."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-debate", plan=PlanTier.PROFESSIONAL, api_key_id="kid-d")
_VALID_KEY = "ak_test_debate123"


def _make_app(fake_service: Any, provider: Any = None) -> FastAPI:
    """Minimal app wired with goals router and optional debate provider."""
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    app.state.goal_service = fake_service
    if provider is not None:
        app.state._app_provider = provider
    return app


def test_goal_submit_debate_mode_accepted() -> None:
    """Submit a goal with workflow_mode=debate returns 202 (normal path)."""
    svc = AsyncMock()
    svc.submit_goal.return_value = {
        "id": "gid-debate-1",
        "goal_id": "gid-debate-1",
        "status": "planning",
        "goal": "What is 2+2?",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "What is 2+2?", "workflow_mode": "debate", "dry_run": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    # debate mode should not cause 4xx/5xx — falls back if no provider
    assert resp.status_code in (200, 201, 202), (
        f"Unexpected status: {resp.status_code} {resp.text}"
    )


def test_goal_submit_debate_mode_calls_service() -> None:
    """Debate mode still calls submit_goal on the underlying service."""
    svc = AsyncMock()
    svc.submit_goal.return_value = {
        "id": "gid-2",
        "goal_id": "gid-2",
        "status": "planning",
        "goal": "Summarise quarterly report",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "Summarise quarterly report", "workflow_mode": "debate"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    svc.submit_goal.assert_called_once()
    call_kwargs = svc.submit_goal.call_args.kwargs
    assert call_kwargs["workflow_mode"] == "debate"


def test_goal_submit_debate_mode_with_mock_provider() -> None:
    """When provider is available debate runs and enriches execution_context."""

    # Mock provider that returns dummy completions
    mock_provider = AsyncMock()
    mock_completion = AsyncMock()
    mock_completion.content = "My proposal for solving this goal effectively."
    mock_provider.complete.return_value = mock_completion

    captured_exec_ctx: dict[str, Any] = {}

    async def fake_submit_goal(**kwargs: Any) -> dict[str, Any]:
        captured_exec_ctx.update(kwargs.get("execution_context") or {})
        return {"id": "gid-3", "goal_id": "gid-3", "status": "planning", "goal": kwargs["goal"]}

    svc = AsyncMock()
    svc.submit_goal.side_effect = fake_submit_goal

    client = TestClient(
        _make_app(svc, provider=mock_provider), raise_server_exceptions=False
    )

    resp = client.post(
        "/goals",
        json={"goal": "Design a caching strategy", "workflow_mode": "debate"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    # Debate keys should be present in execution_context when provider is available
    assert "debate_consensus" in captured_exec_ctx
    assert "debate_confidence" in captured_exec_ctx


def test_goal_submit_non_debate_mode_unchanged() -> None:
    """Non-debate workflow_mode is unaffected by the debate block."""
    svc = AsyncMock()
    svc.submit_goal.return_value = {
        "id": "gid-4",
        "goal_id": "gid-4",
        "status": "planning",
        "goal": "Run analytics",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "Run analytics", "workflow_mode": "single_agent"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    call_kwargs = svc.submit_goal.call_args.kwargs
    assert call_kwargs["workflow_mode"] == "single_agent"
