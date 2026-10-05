"""Comprehensive tests for /guardrails API endpoints — targets 36% → 85%+ coverage."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.guardrails import (
    _configs_store,
)
from app.api.guardrails import (
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
    """Remove the test tenant's configs (guardrails_v2 rules, P8-1) and violations."""
    from app.guardrails_v2.engine import guardrails_engine

    _configs_store.pop(tenant_id, None)
    guardrails_engine._rules.pop(tenant_id, None)
    guardrails_engine._violations.pop(tenant_id, None)


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


def _record(**kw: Any) -> None:
    """A violation in the engine's (repository-less) store — what the API reads."""
    import datetime
    import uuid as _uuid

    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailViolation

    fields: dict[str, Any] = {
        "violation_id": _uuid.uuid4().hex, "tenant_id": _CTX.tenant_id, "rule_id": "r1",
        "rule_name": "rule", "layer": "goal", "action_taken": "block", "category": "pii",
        "content_preview": "***", "severity": "high",
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(), **kw,
    }
    guardrails_engine._remember_violation(_CTX.tenant_id, GuardrailViolation(**fields))


def test_list_violations_with_filters() -> None:
    """Filtering by severity/layer/goal_id should work (P8b-3: durable store)."""
    _clean_store()
    _record(severity="high", layer="goal", goal_id="g1")
    _record(severity="low", layer="tool_args", goal_id="g2")

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/violations?severity=high", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert [v["severity"] for v in resp.json()["violations"]] == ["high"]
    resp = client.get("/guardrails/violations?layer=tool_args", headers={"X-API-Key": _VALID_KEY})
    assert [v["goal_id"] for v in resp.json()["violations"]] == ["g2"]
    resp = client.get("/guardrails/violations?goal_id=g2", headers={"X-API-Key": _VALID_KEY})
    assert resp.json()["total"] == 1
    _clean_store()


def test_list_violations_pagination_is_keyset() -> None:
    _clean_store()
    for i in range(10):
        _record(created_at=f"2026-10-01T00:00:{i:02d}+00:00", rule_name=f"r{i}")
    client = TestClient(_make_app(), raise_server_exceptions=False)
    seen: list[str] = []
    cursor = ""
    for _ in range(5):
        resp = client.get(f"/guardrails/violations?limit=3{cursor}",
                          headers={"X-API-Key": _VALID_KEY})
        assert resp.status_code == 200
        body = resp.json()
        seen += [v["guardrail_name"] for v in body["violations"]]
        if not body["next_cursor"]:
            break
        cursor = f"&cursor={body['next_cursor']}"
    assert seen == [f"r{i}" for i in range(9, -1, -1)]
    resp = client.get("/guardrails/violations?limit=3&offset=2", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 400
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
    assert body["risk_score_p95"] is None


def test_guardrail_stats_with_violations() -> None:
    import datetime

    _clean_store()
    now = datetime.datetime.now(datetime.UTC)
    _record(severity="high", layer="goal", category="injection")
    _record(severity="low", layer="tool_args", category="pii")
    _record(severity="high", layer="goal", category="injection",
            created_at=(now - datetime.timedelta(days=3)).isoformat())
    _record(severity="high", layer="goal", category="injection",
            created_at=(now - datetime.timedelta(days=90)).isoformat())  # outside the window

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/guardrails/stats", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_all"] == body["total_window"] == 3
    assert body["window_days"] == 30
    assert body["total_24h"] == 2
    assert body["by_severity"] == {"high": 2, "low": 1}
    assert body["by_layer"] == {"goal": 2, "tool_args": 1}
    assert body["top_categories"][0] == {"category": "injection", "count": 2}
    _clean_store()
