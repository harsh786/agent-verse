"""Comprehensive tests for /intelligence/export-training-data — targets 18% → 60%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.training_export import router as training_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# Note: training_export.py router has prefix /intelligence
# No auth is required by the endpoint itself (it reads from goal_service)
_CTX = TenantContext(tenant_id="tid-training", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_training_comp"


def _make_app(goal_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(training_router)
    if goal_service:
        app.state.goal_service = goal_service
    return app


def _make_goal_service_with_examples() -> Any:
    """Mock goal service with some completed goals in memory."""
    svc = MagicMock()
    svc._db_session_factory = None

    g1 = MagicMock()
    g1.status = "complete"
    g1.eval_score = 0.9
    g1.goal = "Deploy the app to production"
    g1.result = "App deployed successfully"
    g1.model = "claude-opus-4-5"
    g1.events = [
        {"type": "step_complete", "tool_name": "github.deploy", "output": "Deploy OK"},
    ]

    g2 = MagicMock()
    g2.status = "complete"
    g2.eval_score = 0.95
    g2.goal = "Fix the login bug"
    g2.result = "Bug fixed"
    g2.model = "gpt-4o"
    g2.events = [
        {"type": "step_complete", "tool_name": "github.read", "output": "Found issue"},
        {"type": "step_complete", "tool_name": "github.write", "output": "Patched"},
    ]

    svc._goals = {"gid-1": g1, "gid-2": g2}
    return svc


# ---------------------------------------------------------------------------
# export_training_data — basic
# ---------------------------------------------------------------------------

def test_export_training_data_no_service() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")


def test_export_training_data_openai_format() -> None:
    svc = _make_goal_service_with_examples()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?format=openai", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert "X-Training-Examples" in resp.headers
    content = resp.content.decode()
    # JSONL should have lines, each valid JSON
    if content.strip():
        import json
        for line in content.strip().split("\n"):
            data = json.loads(line)
            assert "messages" in data


def test_export_training_data_anthropic_format() -> None:
    svc = _make_goal_service_with_examples()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?format=anthropic", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    content = resp.content.decode()
    if content.strip():
        import json
        for line in content.strip().split("\n"):
            data = json.loads(line)
            assert "messages" in data
            assert "system" in data


def test_export_training_data_min_score_filter() -> None:
    svc = _make_goal_service_with_examples()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?min_score=0.99", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.headers.get("X-Training-Examples", "0") == "0"


def test_export_training_data_min_score_zero() -> None:
    svc = _make_goal_service_with_examples()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?min_score=0.0", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_export_training_data_limit() -> None:
    svc = _make_goal_service_with_examples()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?limit=1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    count = int(resp.headers.get("X-Training-Examples", "0"))
    assert count <= 1


def test_export_training_data_invalid_format() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?format=invalid", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_export_training_data_min_score_out_of_range() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?min_score=1.5", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_export_training_data_limit_too_large() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data?limit=20000", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Training examples with failing goals (should be excluded)
# ---------------------------------------------------------------------------

def test_export_excludes_failed_goals() -> None:
    svc = MagicMock()
    svc._db_session_factory = None
    failed_goal = MagicMock()
    failed_goal.status = "failed"
    failed_goal.eval_score = 0.9  # High score but failed status
    failed_goal.goal = "Deploy the app"
    svc._goals = {"g-failed": failed_goal}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.headers.get("X-Training-Examples", "0") == "0"


def test_export_excludes_low_score_goals() -> None:
    svc = MagicMock()
    svc._db_session_factory = None
    low_score_goal = MagicMock()
    low_score_goal.status = "complete"
    low_score_goal.eval_score = 0.3  # Below default threshold of 0.8
    low_score_goal.goal = "Run a test"
    low_score_goal.events = [
        {"type": "step_complete", "tool_name": "test", "output": "done"}
    ]
    svc._goals = {"g-low": low_score_goal}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.headers.get("X-Training-Examples", "0") == "0"


# ---------------------------------------------------------------------------
# Format conversion helpers (unit tests)
# ---------------------------------------------------------------------------

def test_to_openai_format() -> None:
    from app.api.training_export import _to_openai_format
    example = {
        "goal": "Fix the bug",
        "result": "Bug fixed",
        "steps": [
            {"type": "step_complete", "tool_name": "github.read", "output": "Found issue"}
        ],
        "eval_score": 0.95,
        "model": "gpt-4o",
    }
    result = _to_openai_format(example)
    assert "messages" in result
    assert result["messages"][0]["role"] == "system"
    assert result["messages"][1]["role"] == "user"
    assert result["messages"][1]["content"] == "Fix the bug"
    assert result["metadata"]["eval_score"] == 0.95


def test_to_anthropic_format() -> None:
    from app.api.training_export import _to_anthropic_format
    example = {
        "goal": "Deploy app",
        "result": "Deployed",
        "steps": [
            {"type": "step_complete", "tool_name": "deploy", "output": "OK"}
        ],
        "eval_score": 0.9,
        "model": "claude-opus-4-5",
    }
    result = _to_anthropic_format(example)
    assert result["system"] == "You are an autonomous AI agent. Execute goals step by step."
    assert result["messages"][0]["role"] == "user"
    assert result["metadata"]["eval_score"] == 0.9
    assert result["metadata"]["model"] == "claude-opus-4-5"


def test_to_openai_format_no_steps() -> None:
    from app.api.training_export import _to_openai_format
    example = {
        "goal": "Simple task",
        "result": "Done",
        "steps": [],
        "eval_score": 0.85,
        "model": "gpt-4o",
    }
    result = _to_openai_format(example)
    # Messages: system + user + final assistant
    assert len(result["messages"]) >= 2


def test_to_anthropic_format_no_steps() -> None:
    from app.api.training_export import _to_anthropic_format
    example = {
        "goal": "Simple task",
        "result": "Done",
        "steps": [],
        "eval_score": 0.85,
        "model": "gpt-4o",
    }
    result = _to_anthropic_format(example)
    assert len(result["messages"]) >= 2
