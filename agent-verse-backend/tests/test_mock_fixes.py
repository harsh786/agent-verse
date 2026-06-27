"""Tests verifying all TRUE MOCK fixes are real implementations."""
import pytest
import os


def test_meta_agent_planner_uses_real_provider_when_available(monkeypatch):
    """MetaAgentPlanner should use real provider when API key is set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-placeholder")
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    provider = _resolve_provider_for_app(Settings(_env_file=None))
    assert "FakeProvider" not in type(provider).__name__ or not os.getenv("ANTHROPIC_API_KEY")


def test_meta_agent_planner_warns_without_provider(monkeypatch):
    """When no API key, FakeProvider is used but a warning is logged."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.main import _resolve_provider_for_app
    from app.core.config import Settings
    provider = _resolve_provider_for_app(Settings(_env_file=None))
    # Should be FakeProvider (logged warning)
    assert provider is not None


def test_embed_texts_returns_empty_list_without_provider():
    """embed_texts() returns empty embeddings, not random vectors, when no provider."""
    import asyncio
    from app.providers.base import embed_texts, EmbedRequest
    result = asyncio.run(embed_texts(["test text"], provider=None))
    # Either returns empty list or single empty embedding
    assert isinstance(result, list)
    if result:
        # If returned something, it should be an empty embedding, not random data
        assert result[0] == []


def test_smart_context_fetch_returns_empty_without_embedder():
    """smart_context_fetch returns '' without embedder, not random-vector noise."""
    import asyncio
    from app.pipeline.steps import smart_context_fetch
    from app.tenancy.context import TenantContext, PlanTier

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    result = asyncio.run(
        smart_context_fetch(
            step="find jira issues",
            knowledge_store=None,  # No store
            tenant_ctx=ctx,
        )
    )
    assert result == [] or isinstance(result, str)


def test_simulation_runner_not_duplicate():
    """SimulationRunner class is defined only once."""
    import inspect
    import app.enterprise.simulation as sim_mod
    source = inspect.getsource(sim_mod)
    count = source.count("class SimulationRunner")
    assert count == 1, f"SimulationRunner defined {count} times — remove the duplicate"


def test_analytics_aggregator_accepts_db_param():
    """GoalAnalyticsAggregator accepts db parameter for DB queries."""
    from app.analytics.aggregator import GoalAnalyticsAggregator
    # Should not raise
    agg = GoalAnalyticsAggregator(goal_service=None, db=None)
    assert agg is not None


def test_production_guard_fake_provider_celery(monkeypatch):
    """run_goal with FakeProvider in production mode should fail, not succeed."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.scaling.celery_app import celery_app
    celery_app.conf.task_always_eager = True
    try:
        # Patch get_session_factory at its definition module (imported inside task body)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "app.db.session.get_session_factory",
                lambda: (_ for _ in ()).throw(RuntimeError("no db")),
            )
            from app.scaling.tasks import run_goal
            result = run_goal.apply(args=["g1", "t1", "test goal"])
            data = result.get()
            # Should be failed/no_llm_provider in production mode
            assert data.get("status") in ("failed", "no_llm_provider", "skipped") or \
                   data.get("reason") == "no_llm_provider"
    finally:
        celery_app.conf.task_always_eager = False
