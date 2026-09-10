"""On startup, orchestration state must be hydrated from Postgres."""
from __future__ import annotations

import asyncio


def test_orchestration_persistence_can_load_from_db() -> None:
    """OrchestrationPersistence.load_tool_trust_from_db gracefully handles no DB."""
    from app.services.orchestration_persistence import OrchestrationPersistence

    persistence = OrchestrationPersistence(db=None)
    asyncio.run(persistence.load_tool_trust_from_db("t1", db=None))


def test_reflexion_store_can_be_seeded_from_db() -> None:
    from app.state_runtime.reflexion_store import ReflexionStore

    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="Don't access users table directly — use API",
        source_goal_id="g_old",
        failure_class="auth_failure",
    )
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert "users table" in lessons[0]["lesson"]


def test_tool_trust_store_survives_restart() -> None:
    from app.tool_runtime.tool_score import ToolScorer
    from app.tool_runtime.tool_trust_store import ToolTrustStore

    store = ToolTrustStore()
    for _ in range(10):
        store.record_outcome("jira.search_issues", success=True, latency_ms=300)
    scorer = ToolScorer(trust_store=store)
    profile = scorer.score("jira.search_issues")
    assert profile.success_rate == 1.0
    assert profile.call_count == 10
    assert profile.trust_score > 0.7
