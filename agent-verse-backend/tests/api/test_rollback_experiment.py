"""Tests for POST /experiments/{experiment_id}/rollback API endpoint."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import intelligence_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-rollback",
    plan=PlanTier.ENTERPRISE,
    api_key_id="kid-rb",
)
_VALID_KEY = "av_test_rollback_key"

EXPERIMENT_ID = "exp-001"
AGENT_ID = "agent-abc123"


def _make_app(self_opt_v2: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(intelligence_router)

    if self_opt_v2 is not None:
        app.state.self_optimizer_v2 = self_opt_v2

    return app


def _make_opt_v2(
    experiments: list | None = None,
    rollback_result: bool = True,
) -> Any:
    """Build a mock SelfOptimizerV2 with the given experiments list."""
    svc = MagicMock()
    svc.list_experiments = AsyncMock(
        return_value=experiments
        if experiments is not None
        else [
            {
                "id": EXPERIMENT_ID,
                "agent_id": AGENT_ID,
                "status": "concluded",
                "name": "Test Experiment",
                "lift_pct": 5.0,
            }
        ]
    )
    svc.rollback = AsyncMock(return_value=rollback_result)
    return svc


# ── Tests ─────────────────────────────────────────────────────────────────────

_BASE = "/intelligence"


def test_rollback_succeeds():
    """POST /intelligence/experiments/{id}/rollback → 200 with rolled_back status."""
    opt = _make_opt_v2()
    client = TestClient(_make_app(opt), raise_server_exceptions=False)
    resp = client.post(
        f"{_BASE}/experiments/{EXPERIMENT_ID}/rollback",
        json={"reason": "Testing rollback"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["experiment_id"] == EXPERIMENT_ID
    assert body["status"] == "rolled_back"
    assert body["reason"] == "Testing rollback"
    assert body["agent_id"] == AGENT_ID


def test_rollback_not_found():
    """POST with unknown experiment ID → 404."""
    opt = _make_opt_v2(experiments=[])  # empty — experiment not found
    client = TestClient(_make_app(opt), raise_server_exceptions=False)
    resp = client.post(
        f"{_BASE}/experiments/nonexistent-exp/rollback",
        json={"reason": "shouldn't matter"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_rollback_fails_when_optimizer_returns_false():
    """When rollback() returns False → 400 Bad Request."""
    opt = _make_opt_v2(rollback_result=False)
    client = TestClient(_make_app(opt), raise_server_exceptions=False)
    resp = client.post(
        f"{_BASE}/experiments/{EXPERIMENT_ID}/rollback",
        json={"reason": "should fail"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 400


def test_rollback_503_when_no_optimizer():
    """When self_optimizer_v2 not wired on app.state → 503."""
    client = TestClient(_make_app(self_opt_v2=None), raise_server_exceptions=False)
    resp = client.post(
        f"{_BASE}/experiments/{EXPERIMENT_ID}/rollback",
        json={"reason": "no optimizer"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_rollback_uses_default_reason():
    """Empty body → default reason string applied."""
    opt = _make_opt_v2()
    client = TestClient(_make_app(opt), raise_server_exceptions=False)
    resp = client.post(
        f"{_BASE}/experiments/{EXPERIMENT_ID}/rollback",
        json={},  # no reason provided
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert "Manual rollback" in resp.json()["reason"]


def test_rollback_unauthorized():
    """Missing API key → 401."""
    opt = _make_opt_v2()
    client = TestClient(_make_app(opt), raise_server_exceptions=False)
    resp = client.post(
        f"{_BASE}/experiments/{EXPERIMENT_ID}/rollback",
        json={"reason": "unauthorized attempt"},
        # No X-API-Key header
    )
    assert resp.status_code == 401
