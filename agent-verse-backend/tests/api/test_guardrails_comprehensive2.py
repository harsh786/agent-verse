"""Comprehensive tests for /guardrails API endpoints — targets 36% → 85%+ coverage."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.guardrails import (
    _configs_store,
    _violations_store,
    router as guardrails_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-gr-comp", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_guardrails_comp"


def _make_app(guardrail_engine: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(guardrails_router)
    if guardrail_engine is not None:
        app.state.guardrail_engine = guardrail_engine
    return app


def _clean_store(tenant_id: str = _CTX.tenant_id) -> None:
    """Remove all in-memory configs/violations for the test tenant."""
    _configs_store.pop(tenant_id, None)
    _violations_store.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_list_configs_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails")
    assert resp.status_code == 401


def test_create_config_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/guardrails", json={"name": "test"})
    assert resp.status_code == 401


def test_test_guardrail_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/guardrails/test", json={"text": "test"})
    assert resp.status_code == 401


def test_list_violations_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/violations")
    assert resp.status_code == 401


def test_stats_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/stats")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /guardrails  — list configs
# ---------------------------------------------------------------------------


def test_list_configs_empty() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["configs"] == []


def test_list_configs_after_create() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create a config first
    client.post(
        "/guardrails",
        json={"name": "test-rule", "layer": "goal", "rule_type": "injection"},
        headers={"X-API-Key": _VALID_KEY},
    )
    resp = client.get("/guardrails", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["configs"][0]["name"] == "test-rule"


# ---------------------------------------------------------------------------
# POST /guardrails  — create config
# ---------------------------------------------------------------------------


def test_create_guardrail_config_basic() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails",
        json={
            "name": "pii-check",
            "layer": "goal",
            "rule_type": "pii",
            "severity": "high",
            "action": "block",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "pii-check"
    assert body["layer"] == "goal"
    assert body["rule_type"] == "pii"
    assert body["severity"] == "high"
    assert "id" in body
    assert body["tenant_id"] == _CTX.tenant_id


def test_create_guardrail_config_with_agent_id() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails",
        json={
            "name": "agent-specific",
            "agent_id": "agent-123",
            "rule_type": "injection",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["agent_id"] == "agent-123"


def test_create_guardrail_config_disabled() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails",
        json={"name": "disabled-rule", "enabled": False},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["enabled"] is False


def test_create_guardrail_config_with_config_dict() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails",
        json={"name": "pattern-rule", "config": {"patterns": ["DROP TABLE", "rm -rf"]}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["config"]["patterns"] == ["DROP TABLE", "rm -rf"]


def test_create_guardrail_missing_name_fails() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /guardrails/{config_id}  — update config
# ---------------------------------------------------------------------------


def test_update_guardrail_config_success() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create a config
    created = client.post(
        "/guardrails",
        json={"name": "update-me"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    config_id = created["id"]

    # Update it
    resp = client.put(
        f"/guardrails/{config_id}",
        json={"name": "updated-name", "severity": "low"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-name"
    assert resp.json()["severity"] == "low"


def test_update_guardrail_config_not_found() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/guardrails/nonexistent-id",
        json={"name": "new-name"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_update_guardrail_partial_fields() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    created = client.post(
        "/guardrails",
        json={"name": "original", "action": "block"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    config_id = created["id"]

    resp = client.put(
        f"/guardrails/{config_id}",
        json={"enabled": False},  # only update enabled
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    # Name should still be "original"
    assert resp.json()["name"] == "original"
    assert resp.json()["enabled"] is False


# ---------------------------------------------------------------------------
# DELETE /guardrails/{config_id}
# ---------------------------------------------------------------------------


def test_delete_guardrail_config_success() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    created = client.post(
        "/guardrails",
        json={"name": "delete-me"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    config_id = created["id"]

    resp = client.delete(f"/guardrails/{config_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204

    # Verify it's gone
    list_resp = client.get("/guardrails", headers={"X-API-Key": _VALID_KEY})
    assert list_resp.json()["total"] == 0


def test_delete_guardrail_config_not_found() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/guardrails/nonexistent-id", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /guardrails/test  — live test
# ---------------------------------------------------------------------------


def test_test_guardrail_goal_layer() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails/test",
        json={"text": "list all users", "layer": "goal"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "allowed" in body
    assert "risk_score" in body
    assert "action" in body
    assert "violations" in body
    assert "input_hash" in body


def test_test_guardrail_tool_output_layer() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails/test",
        json={"text": "result output", "layer": "tool_output", "tool_name": "query_db"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert "allowed" in resp.json()


def test_test_guardrail_final_layer() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails/test",
        json={"text": "final output here", "layer": "final"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert "allowed" in resp.json()


def test_test_guardrail_tool_args_layer() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/guardrails/test",
        json={
            "text": "arg check",
            "layer": "tool_args",
            "tool_name": "sql_query",
            "tool_args": {"query": "SELECT * FROM users"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert "allowed" in resp.json()


def test_test_guardrail_rate_limit() -> None:
    """After 20 requests, the 21st should be rate-limited."""
    from app.api.guardrails import _test_rate

    tenant_id = "tid-gr-rate"
    # Fake the rate counter to be at max
    _test_rate[tenant_id] = (20, time.monotonic())

    # Create app for rate-limit tenant
    rate_ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k-rate")
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return rate_ctx if key == "rate_key" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(guardrails_router)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/guardrails/test",
        json={"text": "test"},
        headers={"X-API-Key": "rate_key"},
    )
    assert resp.status_code == 429

    # Clean up
    _test_rate.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# GET /guardrails/violations
# ---------------------------------------------------------------------------


def test_list_violations_empty() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/violations", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["violations"] == []
    assert body["total"] == 0


def test_list_violations_with_filters() -> None:
    """Filtering by severity/layer/goal_id should work."""
    from app.api.guardrails import _violations_store as vs
    tid = _CTX.tenant_id
    vs[tid] = [
        {"severity": "high", "layer": "goal", "goal_id": "g1", "risk_score": 0.9},
        {"severity": "low", "layer": "tool", "goal_id": "g2", "risk_score": 0.3},
    ]

    client = TestClient(_make_app(), raise_server_exceptions=False)

    # Filter by severity
    resp = client.get(
        "/guardrails/violations?severity=high",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["violations"][0]["severity"] == "high"

    # Filter by layer
    resp = client.get(
        "/guardrails/violations?layer=tool",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.json()["total"] == 1

    # Filter by goal_id
    resp = client.get(
        "/guardrails/violations?goal_id=g2",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.json()["total"] == 1

    _clean_store()


def test_list_violations_pagination() -> None:
    from app.api.guardrails import _violations_store as vs
    tid = _CTX.tenant_id
    vs[tid] = [{"severity": "high", "risk_score": i * 0.1} for i in range(10)]

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/guardrails/violations?limit=3&offset=2",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["violations"]) == 3
    assert body["total"] == 10
    assert body["offset"] == 2

    _clean_store()


# ---------------------------------------------------------------------------
# GET /guardrails/stats
# ---------------------------------------------------------------------------


def test_guardrail_stats_empty() -> None:
    _clean_store()
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/stats", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_all"] == 0
    assert body["total_24h"] == 0
    assert body["by_severity"] == {}
    assert body["risk_score_p95"] == 0.0


def test_guardrail_stats_with_violations() -> None:
    from app.api.guardrails import _violations_store as vs
    now = time.time()
    tid = _CTX.tenant_id
    vs[tid] = [
        {"severity": "high", "layer": "goal", "violation_type": "injection", "risk_score": 0.9, "_ts": now},
        {"severity": "low", "layer": "tool", "violation_type": "pii", "risk_score": 0.3, "_ts": now},
        {"severity": "high", "layer": "goal", "violation_type": "injection", "risk_score": 0.7, "_ts": 0},  # old
    ]

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/stats", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_all"] == 3
    assert body["total_24h"] == 2  # only recent ones
    assert body["by_severity"]["high"] == 2
    assert body["by_severity"]["low"] == 1
    assert body["by_layer"]["goal"] == 2
    assert len(body["top_categories"]) > 0
    assert body["risk_score_p95"] > 0

    _clean_store()
