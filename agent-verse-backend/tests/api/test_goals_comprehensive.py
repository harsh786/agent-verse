"""Comprehensive tests for /goals endpoints — targets 28% → 60%+ coverage."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.core.errors import NotFoundError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-goals", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_goals_comp"


def _make_app(fake_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    svc = fake_service or AsyncMock()
    app.state.goal_service = svc
    return app


def _make_goal(gid: str = "gid-1", status: str = "complete") -> dict:
    return {"id": gid, "goal_id": gid, "status": status, "goal": "do it", "priority": "normal", "dry_run": False}


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_submit_goal_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/goals", json={"goal": "do it"})
    assert resp.status_code == 401


def test_list_goals_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/goals")
    assert resp.status_code == 401


def test_get_goal_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_submit_goal_validates_empty_goal() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/goals", json={"goal": ""}, headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_submit_goal_validates_goal_too_long() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={"goal": "x" * 10_001},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# submit_goal — basic
# ---------------------------------------------------------------------------

def test_submit_goal_returns_202() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = _make_goal("gid-1", "planning")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={"goal": "Fix the memory leak"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    assert resp.json()["id"] == "gid-1"


def test_submit_goal_dry_run() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = _make_goal("gid-dry")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={"goal": "Test dry run", "dry_run": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    call_kwargs = svc.submit_goal.call_args.kwargs
    assert call_kwargs["dry_run"] is True


def test_submit_goal_with_agent_id() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = _make_goal("gid-2")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={"goal": "Fix the leak", "agent_id": "agent-x"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    assert svc.submit_goal.call_args.kwargs["agent_id"] == "agent-x"


def test_submit_goal_priority() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = _make_goal("gid-3")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={"goal": "Urgent task", "priority": "high"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    assert svc.submit_goal.call_args.kwargs["priority"] == "high"


# ---------------------------------------------------------------------------
# submit_goal — persistence mode
# ---------------------------------------------------------------------------

def test_submit_goal_persistence_mode() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = _make_goal("gid-persist")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={
            "goal": "Complete the task no matter what",
            "persistence_mode": True,
            "persistence_config": {"max_attempts": 5, "base_backoff_seconds": 60.0},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    call_kwargs = svc.submit_goal.call_args.kwargs
    assert call_kwargs["execution_context"]["persistence_mode"] is True


# ---------------------------------------------------------------------------
# submit_goal — multi_agent with agent_ids
# ---------------------------------------------------------------------------

def test_submit_goal_multi_agent_mode() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"goal_id": "sub-gid-1"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={
            "goal": "Run in parallel",
            "workflow_mode": "multi_agent",
            "agent_ids": ["agent-a", "agent-b"],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "multi_agent"
    assert body["mode"] == "multi_agent"


# ---------------------------------------------------------------------------
# list_goals
# ---------------------------------------------------------------------------

def test_list_goals_returns_goals() -> None:
    svc = AsyncMock()
    svc.list_goals.return_value = {"goals": [_make_goal()]}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert len(resp.json()["goals"]) == 1


def test_list_goals_empty() -> None:
    svc = AsyncMock()
    svc.list_goals.return_value = {"goals": []}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["goals"] == []


# ---------------------------------------------------------------------------
# get_goal_metrics and get_cost_metrics
# ---------------------------------------------------------------------------

def test_get_goal_metrics() -> None:
    svc = AsyncMock()
    svc.get_metrics.return_value = {
        "total_goals": 10,
        "completed": 8,
        "failed": 2,
        "cost_today_usd": 1.5,
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/metrics", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["total_goals"] == 10


def test_get_cost_metrics() -> None:
    svc = AsyncMock()
    svc.get_metrics.return_value = {
        "total_goals": 5,
        "completed": 4,
        "failed": 1,
        "cost_today_usd": 2.5,
    }
    app = _make_app(svc)
    from app.governance.cost import BudgetConfig
    app.state._budget_config = {_CTX.tenant_id: BudgetConfig(per_tenant_daily_usd=100.0, per_goal_usd=10.0)}
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/goals/cost-metrics", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "daily_budget_usd" in body
    assert body["budget_utilization"] == pytest.approx(0.025)


# ---------------------------------------------------------------------------
# preview_routing
# ---------------------------------------------------------------------------

def test_preview_routing_no_router() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get(
        "/goals/route",
        params={"goal": "fix bug"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "no_router"


def test_preview_routing_with_router() -> None:
    svc = AsyncMock()
    app = _make_app(svc)
    decision = MagicMock()
    decision.to_dict.return_value = {"mode": "single", "agent_id": "agent-x"}
    agent_router = AsyncMock()
    agent_router.route = AsyncMock(return_value=decision)
    app.state.agent_router = agent_router

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/goals/route",
        params={"goal": "fix bug"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "single"


# ---------------------------------------------------------------------------
# get_goal
# ---------------------------------------------------------------------------

def test_get_goal_returns_goal() -> None:
    svc = AsyncMock()
    svc.get_goal.return_value = _make_goal()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["id"] == "gid-1"


def test_get_goal_not_found() -> None:
    svc = AsyncMock()
    svc.get_goal.side_effect = NotFoundError("Goal not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# cancel_goal
# ---------------------------------------------------------------------------

def test_cancel_goal() -> None:
    svc = AsyncMock()
    svc.cancel_goal.return_value = {"goal_id": "gid-1", "status": "cancelled"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/gid-1/cancel", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# stream_goal (SSE)
# ---------------------------------------------------------------------------

def test_stream_goal_returns_event_stream() -> None:
    async def _gen(goal_id, tenant_ctx):
        yield {"type": "goal_started", "goal": "do it"}
        yield {"type": "goal_complete"}

    svc = AsyncMock()
    svc.subscribe_events = _gen
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    with client.stream("GET", "/goals/gid-1/stream", headers={"X-API-Key": _VALID_KEY}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        content = resp.read().decode()
    assert "goal_started" in content
    assert "goal_complete" in content


def test_stream_goal_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1/stream")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------

def test_get_audit_log_success() -> None:
    svc = AsyncMock()
    svc.get_goal.return_value = _make_goal()
    svc.get_audit_entries.return_value = [
        {"event_id": "e1", "goal_id": "gid-1", "tool_name": "search"}
    ]
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1/audit", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()[0]["event_id"] == "e1"


def test_get_audit_log_not_found() -> None:
    svc = AsyncMock()
    svc.get_audit_entries.side_effect = NotFoundError("Goal not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/bad-id/audit", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# goal eval
# ---------------------------------------------------------------------------

def test_get_goal_eval_success() -> None:
    svc = AsyncMock()
    svc.get_eval.return_value = {"goal_id": "gid-1", "score": 0.9, "evaluated": True}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1/eval", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["score"] == 0.9


def test_get_goal_eval_not_found() -> None:
    svc = AsyncMock()
    svc.get_eval.side_effect = NotFoundError("Goal not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/bad-id/eval", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# approve_goal
# ---------------------------------------------------------------------------

def test_approve_goal_success() -> None:
    svc = AsyncMock()
    svc.handle_approval.return_value = {"status": "approved", "request_id": "req-1"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/approve",
        json={"request_id": "req-1", "action": "approve", "approver": "ops@co.com"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_approve_goal_not_found() -> None:
    svc = AsyncMock()
    svc.handle_approval.side_effect = NotFoundError("Goal not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/bad-id/approve",
        json={"request_id": "req-1", "action": "approve", "approver": "ops@co.com"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------

def test_pause_goal_success() -> None:
    svc = AsyncMock()
    svc.pause_goal.return_value = {"goal_id": "gid-1", "status": "paused"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/gid-1/pause", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_pause_goal_not_found() -> None:
    svc = AsyncMock()
    svc.pause_goal.side_effect = NotFoundError("not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/bad/pause", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_pause_goal_bad_state() -> None:
    svc = AsyncMock()
    svc.pause_goal.side_effect = ValueError("Goal already paused")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/gid-1/pause", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 400


def test_resume_goal_success() -> None:
    svc = AsyncMock()
    svc.resume_goal.return_value = {"goal_id": "gid-1", "status": "running"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/gid-1/resume", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_resume_goal_not_found() -> None:
    svc = AsyncMock()
    svc.resume_goal.side_effect = NotFoundError("not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/bad/resume", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_resume_goal_bad_state() -> None:
    svc = AsyncMock()
    svc.resume_goal.side_effect = ValueError("Goal not paused")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/gid-1/resume", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------

def test_submit_batch_goals_success() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"goal_id": "sub-1"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/batch",
        json={"goals": ["Goal A", "Goal B", "Goal C"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["total"] == 3
    assert body["queued"] == 3
    assert body["errors"] == 0
    assert "batch_id" in body


def test_submit_batch_goals_partial_error() -> None:
    svc = AsyncMock()
    svc.submit_goal.side_effect = [{"goal_id": "sub-1"}, Exception("fail"), {"goal_id": "sub-3"}]
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/batch",
        json={"goals": ["Goal A", "Goal B", "Goal C"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["queued"] == 2
    assert body["errors"] == 1


def test_submit_batch_goals_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/goals/batch", json={"goals": ["Goal A"]})
    assert resp.status_code == 401


def test_get_batch_status() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get(
        "/goals/batch/batch-123/status",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["batch_id"] == "batch-123"


# ---------------------------------------------------------------------------
# traces (no DB fallback)
# ---------------------------------------------------------------------------

def test_get_goal_traces_no_db() -> None:
    svc = AsyncMock()
    svc.get_goal.return_value = _make_goal()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1/traces", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_goal_traces_not_found() -> None:
    svc = AsyncMock()
    svc.get_goal.side_effect = NotFoundError("Goal not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/bad/traces", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# lineage (no DB fallback)
# ---------------------------------------------------------------------------

def test_get_goal_lineage_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1/lineage", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["root_goal_id"] == "gid-1"


# ---------------------------------------------------------------------------
# attempts (no DB fallback)
# ---------------------------------------------------------------------------

def test_get_goal_attempts_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1/attempts", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# persistence control endpoints
# ---------------------------------------------------------------------------

def test_abort_persistence_no_redis() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/abort",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_skip_persistence_strategy_no_redis() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/skip-strategy",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_inject_persistence_guidance_no_redis() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/inject-guidance",
        json={"guidance": "Try a different approach"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_abort_persistence_with_redis() -> None:
    svc = AsyncMock()
    app = _make_app(svc)
    redis = AsyncMock()
    redis.setex = AsyncMock()
    app.state._policy_pubsub_redis = redis
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/abort",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "abort_requested"


def test_skip_persistence_strategy_with_redis() -> None:
    svc = AsyncMock()
    app = _make_app(svc)
    redis = AsyncMock()
    redis.setex = AsyncMock()
    app.state._policy_pubsub_redis = redis
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/skip-strategy",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "skip_strategy_requested"


def test_inject_persistence_guidance_with_redis() -> None:
    svc = AsyncMock()
    app = _make_app(svc)
    redis = AsyncMock()
    redis.setex = AsyncMock()
    app.state._policy_pubsub_redis = redis
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/inject-guidance",
        json={"guidance": "Try a different approach"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "guidance_injected"


def test_inject_persistence_guidance_empty_invalid() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/gid-1/persistence/inject-guidance",
        json={"guidance": ""},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422
