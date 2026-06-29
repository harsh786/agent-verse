"""Extended tests for training_export — covers paths missed by the first test file.

Targets: 36% → 85%+ coverage on app/api/training_export.py
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.training_export import (
    _collect_training_examples_memory,
    _to_anthropic_format,
    _to_openai_format,
    router as training_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-training2", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_training2"


def _make_app(goal_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(training_router)
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


def _make_service_with_goals(goals: dict) -> Any:
    svc = MagicMock()
    svc._db_session_factory = None
    svc._goals = goals
    return svc


# ---------------------------------------------------------------------------
# _collect_training_examples_memory  (unit tests for helper function)
# ---------------------------------------------------------------------------


def test_collect_memory_no_service_returns_empty() -> None:
    result = _collect_training_examples_memory(None, 0.8, 100)
    assert result == []


def test_collect_memory_no_goals_attr_returns_empty() -> None:
    svc = MagicMock()
    svc._goals = {}
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert result == []


def test_collect_memory_skips_incomplete_goals() -> None:
    g = MagicMock()
    g.status = "running"
    g.eval_score = 0.9
    g.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]
    svc = _make_service_with_goals({"g1": g})
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert result == []


def test_collect_memory_skips_low_score() -> None:
    g = MagicMock()
    g.status = "complete"
    g.eval_score = 0.5
    g.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]
    svc = _make_service_with_goals({"g1": g})
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert result == []


def test_collect_memory_skips_no_score() -> None:
    g = MagicMock()
    g.status = "complete"
    g.eval_score = None
    g.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]
    svc = _make_service_with_goals({"g1": g})
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert result == []


def test_collect_memory_skips_no_steps() -> None:
    g = MagicMock()
    g.status = "complete"
    g.eval_score = 0.9
    g.events = [{"type": "other_event"}]  # no step_complete events
    svc = _make_service_with_goals({"g1": g})
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert result == []


def test_collect_memory_returns_qualifying_goal() -> None:
    g = MagicMock()
    g.status = "complete"
    g.eval_score = 0.95
    g.goal = "Test goal"
    g.result = "Done"
    g.model = "claude-opus-4-5"
    g.events = [{"type": "step_complete", "tool_name": "tool1", "output": "output1"}]
    svc = _make_service_with_goals({"g1": g})
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert len(result) == 1
    assert result[0]["goal"] == "Test goal"
    assert result[0]["eval_score"] == 0.95
    assert result[0]["model"] == "claude-opus-4-5"


def test_collect_memory_respects_limit() -> None:
    goals = {}
    for i in range(10):
        g = MagicMock()
        g.status = "complete"
        g.eval_score = 0.9
        g.goal = f"Goal {i}"
        g.result = "Done"
        g.model = "test"
        g.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]
        goals[f"g{i}"] = g
    svc = _make_service_with_goals(goals)
    result = _collect_training_examples_memory(svc, 0.8, 3)
    assert len(result) == 3


def test_collect_memory_completed_status_variant() -> None:
    """Status 'completed' (past tense) should also qualify."""
    g = MagicMock()
    g.status = "completed"
    g.eval_score = 0.9
    g.goal = "Test"
    g.result = "Done"
    g.model = "test"
    g.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]
    svc = _make_service_with_goals({"g1": g})
    result = _collect_training_examples_memory(svc, 0.8, 100)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _to_openai_format  (unit tests)
# ---------------------------------------------------------------------------


def test_to_openai_format_basic() -> None:
    example = {
        "goal": "Do something",
        "result": "Done",
        "steps": [],
        "eval_score": 0.9,
    }
    out = _to_openai_format(example)
    assert "messages" in out
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][1]["role"] == "user"
    assert out["messages"][1]["content"] == "Do something"
    assert out["messages"][-1]["role"] == "assistant"
    assert out["messages"][-1]["content"] == "Done"


def test_to_openai_format_with_steps() -> None:
    example = {
        "goal": "Deploy app",
        "result": "Deployed",
        "steps": [
            {"type": "step_complete", "tool_name": "deploy_tool", "output": "Deploy started"},
        ],
        "eval_score": 0.95,
    }
    out = _to_openai_format(example)
    # Should have system + user + step_assistant + result_assistant
    assert len(out["messages"]) >= 3
    step_msgs = [m for m in out["messages"] if m["role"] == "assistant"]
    assert any("deploy_tool" in m["content"] for m in step_msgs)
    assert out["metadata"]["eval_score"] == 0.95


def test_to_openai_format_skips_steps_without_tool_name() -> None:
    example = {
        "goal": "Test",
        "result": "Done",
        "steps": [{"type": "step_complete", "tool_name": "", "output": "out"}],
        "eval_score": 0.9,
    }
    out = _to_openai_format(example)
    # Only system + user + result = 3 messages
    assert len(out["messages"]) == 3


# ---------------------------------------------------------------------------
# _to_anthropic_format  (unit tests)
# ---------------------------------------------------------------------------


def test_to_anthropic_format_basic() -> None:
    example = {
        "goal": "Fix bug",
        "result": "Fixed",
        "steps": [],
        "eval_score": 0.85,
        "model": "claude-opus",
    }
    out = _to_anthropic_format(example)
    assert "system" in out
    assert "messages" in out
    assert out["messages"][0]["role"] == "user"
    assert out["metadata"]["eval_score"] == 0.85
    assert out["metadata"]["model"] == "claude-opus"


def test_to_anthropic_format_with_steps() -> None:
    example = {
        "goal": "Query DB",
        "result": "Query ran",
        "steps": [
            {"tool_name": "db_query", "output": "rows returned"},
        ],
        "eval_score": 0.88,
        "model": "gpt-4o",
    }
    out = _to_anthropic_format(example)
    asst_msgs = [m for m in out["messages"] if m["role"] == "assistant"]
    assert any("db_query" in m["content"] for m in asst_msgs)


# ---------------------------------------------------------------------------
# POST /intelligence/export-training-data (API endpoint tests)
# ---------------------------------------------------------------------------


def test_export_empty_result_returns_empty_jsonl() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/export-training-data", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.headers.get("X-Training-Examples") == "0"
    assert resp.content.decode() == ""


def test_export_openai_format() -> None:
    g = MagicMock()
    g.status = "complete"
    g.eval_score = 0.9
    g.goal = "Build API"
    g.result = "API built"
    g.model = "gpt-4o"
    g.events = [{"type": "step_complete", "tool_name": "code_tool", "output": "coded"}]
    svc = _make_service_with_goals({"g1": g})

    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/intelligence/export-training-data?format=openai",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert content.strip()
    line = json.loads(content.strip().split("\n")[0])
    assert "messages" in line


def test_export_anthropic_format() -> None:
    g = MagicMock()
    g.status = "complete"
    g.eval_score = 0.9
    g.goal = "Refactor code"
    g.result = "Refactored"
    g.model = "claude-opus-4-5"
    g.events = [{"type": "step_complete", "tool_name": "refactor", "output": "done"}]
    svc = _make_service_with_goals({"g1": g})

    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/intelligence/export-training-data?format=anthropic",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert content.strip()
    line = json.loads(content.strip().split("\n")[0])
    assert "system" in line


def test_export_min_score_filter() -> None:
    """Goals below min_score should be excluded."""
    g_high = MagicMock()
    g_high.status = "complete"
    g_high.eval_score = 0.95
    g_high.goal = "High score"
    g_high.result = "Done"
    g_high.model = "test"
    g_high.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]

    g_low = MagicMock()
    g_low.status = "complete"
    g_low.eval_score = 0.7  # below default 0.8
    g_low.goal = "Low score"
    g_low.result = "Done"
    g_low.model = "test"
    g_low.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]

    svc = _make_service_with_goals({"g1": g_high, "g2": g_low})
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/intelligence/export-training-data?min_score=0.8",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Training-Examples") == "1"


def test_export_invalid_format_returns_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/intelligence/export-training-data?format=invalid",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_export_limit_parameter() -> None:
    goals = {}
    for i in range(5):
        g = MagicMock()
        g.status = "complete"
        g.eval_score = 0.9
        g.goal = f"Goal {i}"
        g.result = "Done"
        g.model = "test"
        g.events = [{"type": "step_complete", "tool_name": "t", "output": "o"}]
        goals[f"g{i}"] = g
    svc = _make_service_with_goals(goals)

    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/intelligence/export-training-data?limit=2",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Training-Examples") == "2"


def test_export_content_disposition_header() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/intelligence/export-training-data?format=openai",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "openai" in cd
    assert ".jsonl" in cd
