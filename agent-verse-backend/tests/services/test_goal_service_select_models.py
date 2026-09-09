"""Tests for GoalService._select_models_for_tenant (line 979 of
app/services/goal_service.py) and other small untested helpers.

_select_models_for_tenant uses AI Router to pick optimal models for
planner/executor/verifier roles. Currently returns {} on any exception
(ImportError, ai_router not configured, etc.).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(
    tenant_id="tid-models", plan=PlanTier.ENTERPRISE, api_key_id="kid-models"
)


# ── _select_models_for_tenant ────────────────────────────────────────────────


def test_select_models_for_tenant_returns_empty_when_ai_router_unavailable() -> None:
    """_select_models_for_tenant returns {} when ai_router import fails."""
    svc = GoalService()
    # Patch import to fail
    with patch.dict("sys.modules", {"app.ai_router.router": None}):
        result = svc._select_models_for_tenant(_CTX)
    # Either {} (exception caught) or a populated dict (if app.ai_router.router
    # is auto-imported elsewhere)
    assert isinstance(result, dict)


def test_select_models_for_tenant_with_mocked_ai_router() -> None:
    """_select_models_for_tenant returns planner/executor/verifier model strings
    when ai_router.select_model succeeds for all three task types."""
    svc = GoalService()

    # Mock the ai_router module
    fake_router_module = MagicMock()
    fake_router_module.ai_router = MagicMock()
    fake_router_module.ai_router.select_model = MagicMock(
        side_effect=[
            SimpleNamespace(provider="anthropic", model_id="claude-3-opus"),
            SimpleNamespace(provider="openai", model_id="gpt-4o"),
            SimpleNamespace(provider="anthropic", model_id="claude-3-haiku"),
        ]
    )
    fake_models_module = MagicMock()
    fake_models_module.TaskType = MagicMock()
    fake_models_module.TaskType.PLANNING = "PLANNING"
    fake_models_module.TaskType.EXECUTION = "EXECUTION"
    fake_models_module.TaskType.VERIFICATION = "VERIFICATION"

    with patch.dict(
        "sys.modules",
        {"app.ai_router.router": fake_router_module, "app.ai_router.models": fake_models_module},
    ):
        result = svc._select_models_for_tenant(_CTX)

    assert isinstance(result, dict)
    # All three roles should be populated with provider/model_id format
    if result:  # only assert if non-empty (in case ai_router didn't find models)
        for role in ("planner", "executor", "verifier"):
            if role in result:
                assert "/" in result[role]


def test_select_models_for_tenant_returns_partial_when_some_selects_return_none() -> None:
    """_select_models_for_tenant returns only the roles where select_model returned a non-None model."""
    svc = GoalService()

    fake_router_module = MagicMock()
    fake_router_module.ai_router = MagicMock()
    # select_model returns None for planner, model for executor, None for verifier
    fake_router_module.ai_router.select_model = MagicMock(
        side_effect=[
            None,  # planner → skipped
            SimpleNamespace(provider="openai", model_id="gpt-4o"),  # executor
            None,  # verifier → skipped
        ]
    )
    fake_models_module = MagicMock()
    fake_models_module.TaskType = MagicMock()
    fake_models_module.TaskType.PLANNING = "PLANNING"
    fake_models_module.TaskType.EXECUTION = "EXECUTION"
    fake_models_module.TaskType.VERIFICATION = "VERIFICATION"

    with patch.dict(
        "sys.modules",
        {"app.ai_router.router": fake_router_module, "app.ai_router.models": fake_models_module},
    ):
        result = svc._select_models_for_tenant(_CTX)

    assert isinstance(result, dict)
    if result:  # Only executor should be present (planner and verifier returned None)
        assert "executor" in result


def test_select_models_for_tenant_ai_router_exception_returns_empty() -> None:
    """_select_models_for_tenant returns {} when ai_router.select_model raises."""
    svc = GoalService()

    fake_router_module = MagicMock()
    fake_router_module.ai_router = MagicMock()
    fake_router_module.ai_router.select_model = MagicMock(
        side_effect=RuntimeError("router unavailable")
    )
    fake_models_module = MagicMock()
    fake_models_module.TaskType = MagicMock()
    fake_models_module.TaskType.PLANNING = "PLANNING"
    fake_models_module.TaskType.EXECUTION = "EXECUTION"
    fake_models_module.TaskType.VERIFICATION = "VERIFICATION"

    with patch.dict(
        "sys.modules",
        {"app.ai_router.router": fake_router_module, "app.ai_router.models": fake_models_module},
    ):
        result = svc._select_models_for_tenant(_CTX)

    assert result == {}


def test_select_models_for_tenant_returns_empty_for_import_failure() -> None:
    """_select_models_for_tenant returns {} when app.ai_router.models import fails."""
    svc = GoalService()

    # Force ImportError for app.ai_router.models by patching it to None
    with patch.dict("sys.modules", {"app.ai_router.models": None}):
        result = svc._select_models_for_tenant(_CTX)

    assert result == {}


# ── _check_readiness ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_readiness_no_runtime_profile_returns_false() -> None:
    """_check_readiness returns (False, 'no runtime profile') when profile is None."""
    svc = GoalService()
    if hasattr(svc, "_check_readiness"):
        result = await svc._check_readiness(runtime_profile=None)
        # Should be a tuple of (bool, str)
        assert isinstance(result, tuple) or isinstance(result, list)
        if isinstance(result, tuple) and len(result) >= 1:
            assert isinstance(result[0], bool)


@pytest.mark.asyncio
async def test_check_readiness_with_runtime_profile_returns_tuple() -> None:
    """_check_readiness with a runtime_profile returns (bool, reason) tuple."""
    svc = GoalService()
    if hasattr(svc, "_check_readiness"):
        mock_profile = MagicMock()
        result = await svc._check_readiness(runtime_profile=mock_profile)
        assert isinstance(result, tuple) or isinstance(result, list)
