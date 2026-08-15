"""Tests for ChatSearchEngine — 8 cases."""

from __future__ import annotations

import pytest

from app.chat.search import ChatSearchEngine

TENANT = "t1"
OTHER = "t2"

MESSAGES = [
    {"id": "m1", "session_id": "s1", "tenant_id": TENANT, "role": "user", "content": "Deploy FastAPI service", "created_at": "2026-01-01"},
    {"id": "m2", "session_id": "s1", "tenant_id": TENANT, "role": "assistant", "content": "I will deploy the FastAPI service", "created_at": "2026-01-01"},
    {"id": "m3", "session_id": "s2", "tenant_id": TENANT, "role": "user", "content": "What is Redis?", "created_at": "2026-01-01"},
    {"id": "m4", "session_id": "s3", "tenant_id": OTHER, "role": "user", "content": "Deploy FastAPI service in other tenant", "created_at": "2026-01-01"},
]


@pytest.fixture()
def engine() -> ChatSearchEngine:
    return ChatSearchEngine()


def test_cross_session_finds_matching_messages(engine: ChatSearchEngine) -> None:
    results = engine.cross_session_search("FastAPI", MESSAGES, TENANT)
    assert len(results) == 2  # m1, m2 (not m4 because different tenant)


def test_cross_session_rls_isolation(engine: ChatSearchEngine) -> None:
    """Other tenant's messages should NOT appear."""
    results = engine.cross_session_search("FastAPI", MESSAGES, TENANT)
    for r in results:
        assert r.session_id != "s3"


def test_cross_session_no_results(engine: ChatSearchEngine) -> None:
    results = engine.cross_session_search("nonexistent_term_xyz", MESSAGES, TENANT)
    assert results == []


def test_within_session_scoped(engine: ChatSearchEngine) -> None:
    results = engine.within_session_search("FastAPI", MESSAGES, TENANT, session_id="s1")
    assert len(results) == 2
    for r in results:
        assert r.session_id == "s1"


def test_within_session_excludes_other_sessions(engine: ChatSearchEngine) -> None:
    results = engine.within_session_search("Redis", MESSAGES, TENANT, session_id="s1")
    assert results == []


def test_snippet_contains_query_term(engine: ChatSearchEngine) -> None:
    results = engine.cross_session_search("FastAPI", MESSAGES, TENANT, limit=1)
    assert len(results) == 1
    assert "FastAPI" in results[0].snippet or "fastapi" in results[0].snippet.lower()


def test_limit_respected(engine: ChatSearchEngine) -> None:
    results = engine.cross_session_search("FastAPI", MESSAGES, TENANT, limit=1)
    assert len(results) == 1


def test_case_insensitive_search(engine: ChatSearchEngine) -> None:
    results = engine.cross_session_search("fastapi", MESSAGES, TENANT)
    assert len(results) >= 1
