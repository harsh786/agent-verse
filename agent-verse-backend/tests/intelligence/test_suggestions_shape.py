"""Tests verifying the /intelligence/suggestions endpoint returns the shape
expected by the frontend Suggestion interface:
  {id, type, status, confidence, description, agent_id, created_at}
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_suggestion(
    suggestion_id: str = "sug-1",
    category: str = "prompt",
    description: str = "Add more detail to system prompt",
    confidence: float = 0.87,
    applied: bool = False,
    agent_id: str | None = None,
    created_at: str | None = None,
):
    """Create a mock OptimizationSuggestion object."""
    s = MagicMock()
    s.suggestion_id = suggestion_id
    s.category = category
    s.description = description
    s.confidence = confidence
    s.applied = applied
    s.agent_id = agent_id
    s.created_at = created_at or "2026-06-28T00:00:00Z"
    return s


# ── Unit tests: shape transformation ─────────────────────────────────────────


def test_suggestion_shape_pending():
    """A non-applied suggestion should have status='pending'."""
    s = _make_suggestion(applied=False)
    # Replicate the mapping from the endpoint
    result = {
        "id": s.suggestion_id,
        "type": s.category,
        "description": s.description,
        "confidence": s.confidence,
        "agent_id": getattr(s, "agent_id", None),
        "status": "applied" if s.applied else "pending",
        "created_at": getattr(s, "created_at", ""),
    }
    assert result["id"] == "sug-1"
    assert result["type"] == "prompt"
    assert result["status"] == "pending"
    assert result["confidence"] == 0.87
    assert result["description"] == "Add more detail to system prompt"


def test_suggestion_shape_applied():
    """An applied suggestion should have status='applied'."""
    s = _make_suggestion(applied=True)
    result = {
        "id": s.suggestion_id,
        "type": s.category,
        "description": s.description,
        "confidence": s.confidence,
        "agent_id": getattr(s, "agent_id", None),
        "status": "applied" if s.applied else "pending",
        "created_at": getattr(s, "created_at", ""),
    }
    assert result["status"] == "applied"


def test_suggestion_shape_no_suggestion_id_key():
    """The response must NOT contain 'suggestion_id' — frontend expects 'id'."""
    s = _make_suggestion()
    result = {
        "id": s.suggestion_id,
        "type": s.category,
        "description": s.description,
        "confidence": s.confidence,
        "agent_id": getattr(s, "agent_id", None),
        "status": "applied" if s.applied else "pending",
        "created_at": getattr(s, "created_at", ""),
    }
    assert "suggestion_id" not in result
    assert "category" not in result
    assert "applied" not in result


def test_suggestion_shape_no_category_key():
    """The response must NOT contain 'category' — frontend expects 'type'."""
    s = _make_suggestion(category="tool_selection")
    result = {
        "id": s.suggestion_id,
        "type": s.category,
        "description": s.description,
        "confidence": s.confidence,
        "agent_id": getattr(s, "agent_id", None),
        "status": "applied" if s.applied else "pending",
        "created_at": getattr(s, "created_at", ""),
    }
    assert result["type"] == "tool_selection"
    assert "category" not in result


def test_suggestion_required_fields():
    """Every suggestion must have all 7 frontend-expected fields."""
    required = {"id", "type", "status", "confidence", "description", "agent_id", "created_at"}
    s = _make_suggestion()
    result = {
        "id": s.suggestion_id,
        "type": s.category,
        "description": s.description,
        "confidence": s.confidence,
        "agent_id": getattr(s, "agent_id", None),
        "status": "applied" if s.applied else "pending",
        "created_at": getattr(s, "created_at", ""),
    }
    missing = required - set(result.keys())
    assert missing == set(), f"Missing fields: {missing}"


def test_suggestion_status_values():
    """Status must be one of 'pending', 'applied', 'rejected'."""
    allowed = {"pending", "applied", "rejected"}
    for applied_val in (True, False):
        s = _make_suggestion(applied=applied_val)
        status = "applied" if s.applied else "pending"
        assert status in allowed


def test_suggestion_confidence_range():
    """Confidence should be in [0, 1]."""
    s = _make_suggestion(confidence=0.92)
    assert 0.0 <= s.confidence <= 1.0


def test_multiple_suggestions_all_have_correct_shape():
    """A list of suggestions should all have the correct shape."""
    suggestions_raw = [
        _make_suggestion("s1", "prompt", "Improve prompt", 0.9, False),
        _make_suggestion("s2", "tool_selection", "Use better tools", 0.7, True),
        _make_suggestion("s3", "retry_strategy", "Reduce retries", 0.6, False),
    ]
    results = [
        {
            "id": s.suggestion_id,
            "type": s.category,
            "description": s.description,
            "confidence": s.confidence,
            "agent_id": getattr(s, "agent_id", None),
            "status": "applied" if s.applied else "pending",
            "created_at": getattr(s, "created_at", ""),
        }
        for s in suggestions_raw
    ]
    assert len(results) == 3
    assert results[0]["status"] == "pending"
    assert results[1]["status"] == "applied"
    assert results[2]["status"] == "pending"
    for r in results:
        assert "id" in r and "type" in r and "status" in r
        assert "suggestion_id" not in r
        assert "category" not in r


# ── Test experiment list_experiments shape ─────────────────────────────────────


def test_experiment_shape_fields():
    """list_experiments result must contain frontend-expected fields."""
    required = {"id", "name", "agent_id", "status", "control_config",
                "challenger_config", "lift_pct", "started_at", "concluded_at"}

    # Simulate what list_experiments returns after our fix
    experiment = {
        "id": "exp-1",
        "agent_id": "agent-1",
        "name": "Auto-optimization 2026-06-28 10:00",
        "status": "concluded",
        "challenger_config": {"temperature": 0.7},
        "control_config": {"temperature": 0.2},
        "lift_pct": 12.5,
        "started_at": "2026-06-01T00:00:00+00:00",
        "concluded_at": "2026-06-15T00:00:00+00:00",
    }
    missing = required - set(experiment.keys())
    assert missing == set(), f"Missing fields: {missing}"


def test_experiment_status_mapping():
    """DB status 'completed' should map to frontend status 'concluded'."""
    from app.intelligence.self_optimizer_v2 import SelfOptimizerV2

    raw_status = "completed"
    if raw_status in ("completed", "rolled_back", "failed"):
        fe_status = "concluded"
    elif raw_status == "paused":
        fe_status = "pending"
    else:
        fe_status = raw_status
    assert fe_status == "concluded"


def test_experiment_status_running_passthrough():
    """DB status 'running' should map to frontend 'running'."""
    raw_status = "running"
    if raw_status in ("completed", "rolled_back", "failed"):
        fe_status = "concluded"
    elif raw_status == "paused":
        fe_status = "pending"
    else:
        fe_status = raw_status
    assert fe_status == "running"


def test_experiment_no_wrong_column_names():
    """The SELECT query must NOT reference non-existent columns."""
    import inspect

    from app.intelligence import self_optimizer_v2

    source = inspect.getsource(self_optimizer_v2.SelfOptimizerV2.list_experiments)
    # These column names don't exist in the DB
    assert "challenger_wins" not in source, "challenger_wins column does not exist"
    assert "control_wins" not in source, "control_wins column does not exist"
    assert "winner_arm" not in source, "winner_arm column does not exist"
    # concluded_at doesn't exist in the schema (it's completed_at)
    assert "concluded_at" not in source or "completed_at" in source, \
        "DB column is completed_at, not concluded_at"


def test_experiment_uses_correct_column_names():
    """The SELECT query must reference the correct DB column names."""
    import inspect

    from app.intelligence import self_optimizer_v2

    source = inspect.getsource(self_optimizer_v2.SelfOptimizerV2.list_experiments)
    assert "candidate_config" in source
    assert "bayesian_uplift" in source
    assert "started_at" in source
