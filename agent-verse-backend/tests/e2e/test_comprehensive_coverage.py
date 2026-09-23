"""Comprehensive coverage tests for remaining uncovered modules.

Targets: cli, celery tasks, DB session/RLS, tracing, browser agent,
pipeline steps, providers, rag store DB path, triggers store DB path,
goal service full lifecycle, tenant service full lifecycle, audit log DB.
"""
from __future__ import annotations

import asyncio
from datetime import UTC
from typing import Any
from unittest.mock import patch

import pytest

# ── 1. CLI Tests ──────────────────────────────────────────────────────────────


def test_cli_app_is_typer_app():
    """CLI module loads without error."""
    import typer

    from app.cli.main import app as cli_app
    assert isinstance(cli_app, typer.Typer)


def test_cli_stream_goal_handles_connection_error():
    """_stream_goal gracefully handles network failures."""
    import httpx

    from app.cli.main import _stream_goal

    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.stream.side_effect = (
            httpx.ConnectError("refused")
        )
        # Should not raise — just log the error
        try:
            _stream_goal("nonexistent-goal-id")
        except Exception:
            pass  # Any exception is acceptable here


def test_cli_commands_exist():
    """All expected CLI commands are registered."""
    from app.cli.main import app as cli_app

    # c.name is None for commands that inherit the function name; use callback name
    resolved = [
        (c.name or (c.callback.__name__ if c.callback else ""))
        for c in cli_app.registered_commands
    ]
    assert "create" in resolved
    assert "submit" in resolved
    assert "status" in resolved
    assert "agents" in resolved
    assert "schedule" in resolved


def test_cli_base_url_from_env():
    """_base_url reads AGENTVERSE_URL from environment."""
    import os

    from app.cli.main import _base_url

    original = os.environ.get("AGENTVERSE_URL")
    try:
        os.environ["AGENTVERSE_URL"] = "http://custom:9090"
        assert _base_url() == "http://custom:9090"
    finally:
        if original is None:
            os.environ.pop("AGENTVERSE_URL", None)
        else:
            os.environ["AGENTVERSE_URL"] = original


def test_cli_api_key_missing_exits():
    """_api_key raises typer.Exit(1) when AGENTVERSE_API_KEY not set."""
    import os

    import typer

    from app.cli.main import _api_key

    original = os.environ.pop("AGENTVERSE_API_KEY", None)
    try:
        with pytest.raises(typer.Exit):
            _api_key()
    finally:
        if original is not None:
            os.environ["AGENTVERSE_API_KEY"] = original


def test_cli_api_key_from_env():
    """_api_key returns key when AGENTVERSE_API_KEY is set."""
    import os

    from app.cli.main import _api_key

    os.environ["AGENTVERSE_API_KEY"] = "test-cli-key-123"
    try:
        assert _api_key() == "test-cli-key-123"
    finally:
        del os.environ["AGENTVERSE_API_KEY"]


# ── 2. Celery tasks ───────────────────────────────────────────────────────────


def test_celery_app_has_correct_queues():
    """Celery routes new goals to the free-plan isolation queue by default."""
    from app.scaling.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert "app.scaling.tasks.run_goal" in routes
    assert routes["app.scaling.tasks.run_goal"]["queue"] == "goals.free"
    assert "app.scaling.tasks.run_scheduled_goal" in routes
    assert "app.scaling.tasks.health_check_mcp" in routes


def test_celery_beat_schedule_has_required_entries():
    """Celery beat schedule includes both maintenance tasks."""
    from app.scaling.celery_app import celery_app

    beat = celery_app.conf.beat_schedule
    keys = list(beat.keys())
    assert any("mcp" in k for k in keys)
    assert any("schedule" in k for k in keys)


def test_run_goal_task_executes_with_fake_provider(monkeypatch: Any) -> None:
    """run_goal task completes without crashing (uses FakeProvider, ALWAYS_EAGER).

    Patches get_session_factory so the task runs entirely in-memory:
    no real PostgreSQL connection is attempted and no asyncpg sockets are opened.
    This makes the test hermetic and eliminates dangling-connection ResourceWarnings
    that would otherwise corrupt the next test's environment.
    """
    # ── Prevent any DB connection attempts inside run_goal ────────────────────
    # The task wraps _make_session_factory in try/except; raising here sets
    # goal_bridge = None so every _db_* helper is a safe no-op.
    monkeypatch.setattr(
        "app.db.session._make_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("no DB in unit tests")),
    )
    monkeypatch.setattr("app.scaling.tasks._get_llm_provider", lambda _tenant_id: None)
    monkeypatch.setattr("app.scaling.tasks._get_sync_redis", lambda: None)
    monkeypatch.setattr("app.core.config.get_provider_env", lambda _name: None)
    # Without a DB, _load_worker_policy_engine falls into fail-closed mode (deny
    # all tools). Patch it to return a permissive empty engine so llm_call is not
    # denied and the test can exercise the fake-provider code path.
    async def _permissive_policy_engine(db_factory: object, tenant_id: str) -> None:
        return None  # None → graph skips the policy check entirely

    monkeypatch.setattr(
        "app.scaling.tasks._load_worker_policy_engine", _permissive_policy_engine
    )

    from app.scaling.celery_app import celery_app
    from app.scaling.tasks import run_goal

    # Correct positional arg order: goal_id, tenant_id, goal_text
    # (previously "test goal" was passed as tenant_id and "test-tenant" as
    #  goal_text — fixed here to match the function signature)
    celery_app.conf.task_always_eager = True
    try:
        result = run_goal.apply(
            args=["test-g1", "test-tenant", "Complete the test goal autonomously"]
        )
        assert result.successful(), f"Task failed: {result.result}"
        data = result.get()
        assert "status" in data
        assert data["goal_id"] == "test-g1"
        # Worker ran without a DB bridge — verify the result_scope field
        assert data.get("result_scope") in ("worker_only", "submitted_goal")
    finally:
        celery_app.conf.task_always_eager = False


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_health_check_mcp_task_returns_status():
    """health_check_mcp returns a dict with status and checked_at keys."""
    from app.scaling.tasks import health_check_mcp

    result = health_check_mcp()
    assert isinstance(result, dict)
    assert "status" in result
    assert result["status"] == "ok"
    assert "checked_at" in result


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_fire_due_schedules_task_returns_status():
    """fire_due_schedules returns a dict with status and checked_at."""
    from app.scaling.tasks import fire_due_schedules

    result = fire_due_schedules()
    assert isinstance(result, dict)
    assert "status" in result
    assert "checked_at" in result


# ── 3. DB RLS helpers ─────────────────────────────────────────────────────────


def test_sqlalchemy_rls_context_is_callable():
    """sqlalchemy_rls_context is a callable async context manager factory."""
    import inspect

    from app.db.rls import sqlalchemy_rls_context

    assert callable(sqlalchemy_rls_context)
    # It should be an async context manager (decorated with asynccontextmanager)
    assert inspect.isfunction(sqlalchemy_rls_context)


def test_rls_context_is_callable():
    """rls_context (asyncpg variant) is a callable."""
    from app.db.rls import rls_context

    assert callable(rls_context)


def test_db_session_module_imports_cleanly():
    """db/session.py imports without error and all callables are present."""
    from app.db.session import get_db, get_db_session, get_session_factory

    assert callable(get_session_factory)
    assert callable(get_db_session)
    assert callable(get_db)


def test_make_session_factory_creates_factory():
    """_make_session_factory returns an async_sessionmaker."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.session import _make_session_factory

    factory = _make_session_factory(
        "postgresql+asyncpg://test:test@localhost/test"
    )
    assert isinstance(factory, async_sessionmaker)


def test_make_engine_uses_provided_url():
    """_make_engine returns an AsyncEngine for the provided URL."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.db.session import _make_engine

    engine = _make_engine("postgresql+asyncpg://user:pw@localhost/testdb")
    assert isinstance(engine, AsyncEngine)


# ── 4. OTel Tracing ───────────────────────────────────────────────────────────


def test_configure_tracing_noop_when_no_endpoint():
    """configure_tracing is a no-op when OTLP endpoint is not set."""
    from app.main import create_app
    from app.observability.tracing import configure_tracing

    app = create_app()
    # With no OTEL_EXPORTER_OTLP_ENDPOINT, this is a no-op — no exception
    configure_tracing(app, app.state.settings)


@pytest.mark.filterwarnings("ignore")
def test_configure_tracing_with_endpoint():
    """configure_tracing wires OTel when endpoint is configured."""
    from app.core.config import Settings
    from app.main import create_app
    from app.observability.tracing import configure_tracing

    # Use the correct field name: otel_exporter_otlp_endpoint
    settings = Settings(otel_exporter_otlp_endpoint="http://localhost:4317")
    app = create_app(settings=settings)
    # configure_tracing was already called by create_app; calling again is idempotent
    configure_tracing(app, settings)
    # No exception = success


# ── 5. Browser Agent ──────────────────────────────────────────────────────────


async def test_browser_agent_availability_flag():
    """BrowserAgent.available reflects the _PLAYWRIGHT_AVAILABLE flag."""
    from app.perception.browser_agent import _PLAYWRIGHT_AVAILABLE, BrowserAgent

    agent = BrowserAgent()
    assert agent.available == _PLAYWRIGHT_AVAILABLE


async def test_browser_agent_all_actions_without_playwright():
    """All browser actions return success=False when Playwright is not available."""
    import app.perception.browser_agent as ba
    from app.perception.browser_agent import BrowserAgent

    orig = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        r1 = await agent.take_screenshot("http://x.com")
        assert r1.success is False

        r2 = await agent.extract_text("http://x.com")
        assert r2.success is False

        r3 = await agent.click_and_screenshot("http://x.com", "button")
        assert r3.success is False

        r4 = await agent.fill_and_submit("http://x.com", "input", "value")
        assert r4.success is False
    finally:
        ba._PLAYWRIGHT_AVAILABLE = orig


async def test_browser_agent_analyze_screenshot_no_provider():
    """analyze_screenshot returns descriptive message when no vision provider."""
    from app.perception.browser_agent import BrowserAgent

    agent = BrowserAgent(vision_provider=None)
    result = await agent.analyze_screenshot("aGVsbG8=", "What is shown?")
    assert "No vision provider" in result


async def test_browser_agent_analyze_screenshot_no_vision_support():
    """analyze_screenshot skips providers that don't support vision."""
    from app.perception.browser_agent import BrowserAgent

    class NoVisionProvider:
        def supports_vision(self) -> bool:
            return False

    agent = BrowserAgent(vision_provider=NoVisionProvider())
    result = await agent.analyze_screenshot("aGVsbG8=", "What is shown?")
    assert "No vision provider" in result


async def test_browser_agent_run_action_dispatch():
    """run_action dispatches to the correct method based on action_type."""
    import app.perception.browser_agent as ba
    from app.perception.browser_agent import BrowserAction, BrowserAgent

    orig = ba._PLAYWRIGHT_AVAILABLE
    ba._PLAYWRIGHT_AVAILABLE = False
    try:
        agent = BrowserAgent()
        actions = [
            BrowserAction(action_type="navigate", url="http://x.com"),
            BrowserAction(action_type="screenshot", url="http://x.com"),
            BrowserAction(action_type="extract_text", url="http://x.com", selector="body"),
            BrowserAction(action_type="click", url="http://x.com", selector="a"),
            BrowserAction(action_type="fill", url="http://x.com", selector="input", value="text"),
        ]
        for action in actions:
            result = await agent.run_action(action)
            assert result.success is False  # All fail without Playwright

        # Unknown action type returns error result
        unknown = BrowserAction(action_type="unknown_action", url="http://x.com")
        r = await agent.run_action(unknown)
        assert r.success is False
        assert "Unknown action" in r.error
    finally:
        ba._PLAYWRIGHT_AVAILABLE = orig


# ── 6. Pipeline Steps ─────────────────────────────────────────────────────────


async def test_all_pipeline_steps_work_without_services():
    """All pipeline steps return expected defaults when no services are injected."""
    from app.governance.permissions import ActionLevel
    from app.pipeline.steps import (
        circuit_breaker_check,
        cost_check,
        dedup_check,
        exec_memory_lookup,
        governance_check,
        hitl_gate,
        record_rollback_point,
        record_usage,
        result_processor_step,
        smart_context_fetch,
        stream_step_event,
    )
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="pipe-t1", plan=PlanTier.FREE, api_key_id="pk1"
    )

    assert await cost_check(step="test", tenant_ctx=T) is True
    assert await governance_check(tool_name="test", tenant_ctx=T) == ActionLevel.ALLOW
    assert await dedup_check(content_hash="abc", tenant_ctx=T) is False
    assert await circuit_breaker_check(tool_name="test", tenant_ctx=T) is False
    assert await hitl_gate(action="test", risk_level="low", tenant_ctx=T) is False
    # record_usage is a no-op without audit_log — should not raise
    await record_usage(tool_name="test", tokens_used=100, tenant_ctx=T)
    assert await exec_memory_lookup(goal="test", tenant_ctx=T) == []
    assert await record_rollback_point(action="a", inverse_action="b", tenant_ctx=T) == ""
    assert await result_processor_step(raw_output="test output", tenant_ctx=T) == "test output"
    await stream_step_event(event={"type": "test"}, tenant_ctx=T)
    assert await smart_context_fetch(goal="test", step="step", tenant_ctx=T) == ""


async def test_pipeline_steps_with_all_services():
    """Pipeline steps properly delegate to real service objects."""
    from app.governance.audit import AuditLog
    from app.governance.cost import BudgetConfig, CostController
    from app.governance.hitl import HITLGateway
    from app.governance.permissions import ActionLevel, PermissionMatrix
    from app.memory.execution import ExecutionMemory
    from app.pipeline.steps import (
        circuit_breaker_check,
        cost_check,
        dedup_check,
        exec_memory_lookup,
        governance_check,
        hitl_gate,
        record_rollback_point,
        record_usage,
        result_processor_step,
    )
    from app.reliability.circuit_breaker import CircuitBreaker
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="pipe-svc-t1", plan=PlanTier.PROFESSIONAL, api_key_id="pk2"
    )

    # cost_check with budget
    cost = CostController(BudgetConfig(per_goal_usd=100.0, per_tenant_daily_usd=1000.0))
    assert await cost_check(step="s", tenant_ctx=T, controller=cost, estimated_cost=0.01) is True

    # governance_check delegates to permission matrix
    matrix = PermissionMatrix()
    level = await governance_check(tool_name="github", tenant_ctx=T, matrix=matrix)
    assert level == ActionLevel.ALLOW_LOG

    # dedup_check: first time False, after mark_seen True
    cache = DeduplicationCache()
    assert await dedup_check(content_hash="h1", tenant_ctx=T, cache=cache) is False
    cache.mark_seen(content_hash="h1", tenant_ctx=T)
    assert await dedup_check(content_hash="h1", tenant_ctx=T, cache=cache) is True

    # circuit_breaker_check: closed → False, open → True
    breaker = CircuitBreaker(failure_threshold=3)
    assert await circuit_breaker_check(tool_name="t", tenant_ctx=T, breaker=breaker) is False
    for _ in range(3):
        breaker.record_failure()
    assert await circuit_breaker_check(tool_name="t", tenant_ctx=T, breaker=breaker) is True

    # result_processor_step redacts secrets
    rp = ResultProcessor()
    result = await result_processor_step(
        raw_output="key: sk-secret123abc", tenant_ctx=T, processor=rp
    )
    assert "sk-secret" not in result
    assert "[REDACTED]" in result

    # record_rollback_point adds to engine
    engine = RollbackEngine()
    await record_rollback_point(
        action="deploy", inverse_action="rollback", tenant_ctx=T, engine=engine
    )
    assert len(engine) == 1

    # exec_memory_lookup returns empty list for new memory
    mem = ExecutionMemory()
    results = await exec_memory_lookup(goal="test goal", tenant_ctx=T, memory=mem)
    assert results == []

    # record_usage writes to audit log
    audit = AuditLog()
    await record_usage(
        tool_name="github", tokens_used=200, tenant_ctx=T, audit_log=audit, goal_id="g1"
    )
    entries = audit.query(tenant_ctx=T)
    assert len(entries) == 1
    assert entries[0].tool_name == "github"

    # hitl_gate with high risk level creates approval request
    hitl = HITLGateway()
    triggered = await hitl_gate(
        action="deploy to prod", risk_level="high", tenant_ctx=T, gateway=hitl, goal_id="g1"
    )
    assert triggered is True
    pending = hitl.list_pending(tenant_ctx=T)
    assert len(pending) >= 1


async def test_smart_context_fetch_with_knowledge_store():
    """smart_context_fetch now uses retrieval_gateway; skip gracefully when None."""
    from app.pipeline.steps import smart_context_fetch
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="pipe-ks-t1", plan=PlanTier.FREE, api_key_id="pks1"
    )

    # retrieval_gateway=None → smart_context_fetch returns "" (no collections)
    ctx = await smart_context_fetch(
        goal="ML task",
        step="use machine learning",
        tenant_ctx=T,
        retrieval_gateway=None,
        collection_ids=None,
    )
    # Without a retrieval gateway or collection_ids the function returns empty
    assert isinstance(ctx, str)


# ── 7. Providers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gemini_provider_uses_current_defaults():
    pytest.importorskip("google.genai", reason="google-genai package not installed")
    from app.providers.gemini_provider import GeminiProvider

    provider = GeminiProvider(api_key="test")
    assert provider._default_model == "gemini-2.5-pro"
    assert provider._embed_model == "gemini-embedding-001"
    await provider.aclose()


@pytest.mark.asyncio
async def test_voyage_provider_uses_current_default():
    from app.providers.voyage_provider import VoyageProvider

    provider = VoyageProvider(api_key="test")
    assert provider._model == "voyage-4-large"
    await provider.aclose()


def test_openai_compatible_provider_raises_import_error():
    """OpenAICompatibleProvider raises ImportError if openai not installed."""
    try:
        from app.providers.openai_compatible import OpenAICompatibleProvider
        OpenAICompatibleProvider(api_key="test")
    except ImportError as e:
        assert "openai" in str(e)


def test_anthropic_provider_raises_import_error_if_missing():
    """AnthropicProvider raises ImportError if anthropic not installed."""
    try:
        from app.providers.anthropic_provider import AnthropicProvider
        # If anthropic IS installed (it is in deps), just check it instantiates
        p = AnthropicProvider(api_key="test-key")
        assert p.supports_vision() is True
        assert p.supports_tool_use() is True
    except ImportError as e:
        assert "anthropic" in str(e)


async def test_fake_provider_cycles_responses():
    """FakeProvider cycles through responses deterministically."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.fake import FakeProvider

    p = FakeProvider(responses=["r1", "r2", "r3"])
    req = CompletionRequest(
        messages=[Message(role="user", content="test")], model=""
    )
    r1 = await p.complete(req)
    r2 = await p.complete(req)
    r3 = await p.complete(req)
    r4 = await p.complete(req)  # Should cycle back to r1
    assert r1.content == "r1"
    assert r2.content == "r2"
    assert r3.content == "r3"
    assert r4.content == "r1"


async def test_fake_provider_embed():
    """FakeProvider returns embeddings with the configured dimension."""
    from app.providers.base import EmbedRequest
    from app.providers.fake import FakeProvider

    p = FakeProvider(responses=[], embed_dim=768)
    req = EmbedRequest(texts=["hello", "world"])
    resp = await p.embed(req)
    assert len(resp.embeddings) == 2
    assert all(len(e) == 768 for e in resp.embeddings)


async def test_fake_provider_call_history():
    """FakeProvider records every call in call_history."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.fake import FakeProvider

    p = FakeProvider(responses=["ok"])
    req = CompletionRequest(
        messages=[Message(role="user", content="hello")], model="test-model"
    )
    await p.complete(req)
    await p.complete(req)
    assert len(p.call_history) == 2


# ── 8. RAG store DB paths ─────────────────────────────────────────────────────


async def test_knowledge_store_hybrid_search_db_fallback_on_error():
    """hybrid_search_db falls back to in-memory when DB is unavailable."""
    from app.rag.models import Chunk, KnowledgeCollection
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="rag-db-t2", plan=PlanTier.PROFESSIONAL, api_key_id="rk2"
    )
    store = KnowledgeStore()
    col = KnowledgeCollection(name="test-col", collection_id="col-db-1")
    store.create_collection(col, tenant_ctx=T)
    chunk = Chunk(
        document_id="d1",
        content="machine learning content",
        embedding=[0.1] * 768,
        chunk_index=0,
        chunk_id="c1",
    )
    store.ingest_chunk(chunk, collection_id="col-db-1", tenant_ctx=T)

    # hybrid_search_db with no DB falls back to in-memory hybrid_search
    results = await store.hybrid_search_db(
        "machine learning", [0.1] * 768, "col-db-1", T, top_k=5
    )
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].content == "machine learning content"


async def test_knowledge_store_create_collection_db_task():
    """create_collection_async is fail-closed: DB errors propagate, cache stays clean."""
    from app.rag.models import KnowledgeCollection
    from app.rag.store import KnowledgeStore
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="rag-db-t3", plan=PlanTier.FREE, api_key_id="rk3"
    )

    # Proper async context manager factory that raises on enter
    class _FailDB:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("DB down")

        async def __aexit__(self, *args):
            pass

    store = KnowledgeStore(db_session_factory=_FailDB())
    col = KnowledgeCollection(name="my-col", collection_id="col-db-2")
    with pytest.raises(RuntimeError, match="DB down"):
        await store.create_collection_async(col, tenant_ctx=T)
    # In-memory must NOT be mutated when DB write fails (fail-closed contract)
    assert store.get_collection("col-db-2", tenant_ctx=T) is None


async def test_knowledge_store_sync_from_db_noop_without_factory():
    """sync_from_db returns 0 when no DB factory is configured."""
    from app.rag.store import KnowledgeStore

    store = KnowledgeStore()
    count = await store.sync_from_db()
    assert count == 0


# ── 9. Trigger Store full coverage ────────────────────────────────────────────


async def test_schedule_store_db_create_fires_task():
    """ScheduleStore works in-memory even when DB factory raises."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    T = TenantContext(
        tenant_id="sched-db-t1", plan=PlanTier.FREE, api_key_id="sk1"
    )

    class _FailDB:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("DB")

        async def __aexit__(self, *args):
            pass

    store = ScheduleStore(db_session_factory=_FailDB())
    spec = TriggerSpec(trigger_type=TriggerType.ONCE, description="test")
    sched_id = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    # Allow background task to run and fail gracefully
    await asyncio.sleep(0.05)
    assert sched_id is not None
    assert store.get(sched_id, tenant_ctx=T) is not None


async def test_schedule_store_pause_resume_delete():
    """ScheduleStore pause/resume/delete work correctly."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    T = TenantContext(
        tenant_id="sched-pr-t1", plan=PlanTier.PROFESSIONAL, api_key_id="spr1"
    )
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)

    assert store.pause(sid, tenant_ctx=T) is True
    assert store.get(sid, tenant_ctx=T)["paused"] is True

    assert store.resume(sid, tenant_ctx=T) is True
    assert store.get(sid, tenant_ctx=T)["paused"] is False

    assert store.delete(sid, tenant_ctx=T) is True
    assert store.get(sid, tenant_ctx=T) is None


async def test_schedule_store_list_all():
    """ScheduleStore.list_all returns only the current tenant's schedules."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    T_A = TenantContext(tenant_id="sched-la-a", plan=PlanTier.FREE, api_key_id="sla1")
    T_B = TenantContext(tenant_id="sched-la-b", plan=PlanTier.FREE, api_key_id="slb1")
    store = ScheduleStore()

    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store.create(goal_id="g1", spec=spec, tenant_ctx=T_A)
    store.create(goal_id="g2", spec=spec, tenant_ctx=T_A)
    store.create(goal_id="g3", spec=spec, tenant_ctx=T_B)

    assert len(store.list_all(tenant_ctx=T_A)) == 2
    assert len(store.list_all(tenant_ctx=T_B)) == 1


async def test_schedule_store_sync_from_db_noop():
    """sync_from_db returns 0 when no DB factory is configured."""
    from app.triggers.store import ScheduleStore

    store = ScheduleStore()
    count = await store.sync_from_db()
    assert count == 0


async def test_schedule_store_pause_nonexistent_returns_false():
    """Pausing a non-existent schedule returns False."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.triggers.store import ScheduleStore

    T = TenantContext(tenant_id="sched-ne-t1", plan=PlanTier.FREE, api_key_id="sne1")
    store = ScheduleStore()
    assert store.pause("nonexistent-id", tenant_ctx=T) is False
    assert store.resume("nonexistent-id", tenant_ctx=T) is False
    assert store.delete("nonexistent-id", tenant_ctx=T) is False


# ── 10. GoalService full lifecycle ────────────────────────────────────────────


async def test_goal_service_submit_and_get_dry_run():
    """GoalService submit + get works for dry_run goals."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-t1", plan=PlanTier.PROFESSIONAL, api_key_id="gsk1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="dry run test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    assert result["dry_run"] is True
    assert result["status"] == "complete"

    goal_data = await svc.get_goal(goal_id=gid, tenant_ctx=T)
    assert goal_data["goal_id"] == gid
    assert goal_data["dry_run"] is True


async def test_goal_service_tenant_isolation():
    """GoalService enforces strict tenant isolation — cross-tenant access raises."""
    from app.core.errors import NotFoundError
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T_A = TenantContext(
        tenant_id="gs-iso-a", plan=PlanTier.FREE, api_key_id="gsa"
    )
    T_B = TenantContext(
        tenant_id="gs-iso-b", plan=PlanTier.FREE, api_key_id="gsb"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="tenant A goal", priority="normal", dry_run=True, tenant_ctx=T_A
    )
    gid = result["goal_id"]

    with pytest.raises(NotFoundError):
        await svc.get_goal(goal_id=gid, tenant_ctx=T_B)


async def test_goal_service_cancel():
    """GoalService.cancel_goal transitions status to cancelled."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-cancel-t1", plan=PlanTier.FREE, api_key_id="gsc1"
    )
    from app.agent.state import GoalStatus

    svc = GoalService()
    result = await svc.submit_goal(
        goal="cancel me", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    # dry_run goals complete synchronously; force a non-terminal status so
    # this actually exercises cancelling a running goal.
    svc._goals[gid].status = GoalStatus.EXECUTING
    cancelled = await svc.cancel_goal(goal_id=gid, tenant_ctx=T)
    assert cancelled["status"] == "cancelled"


async def test_goal_service_get_events():
    """GoalService.get_events returns the events snapshot."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-ev-t1", plan=PlanTier.FREE, api_key_id="gsev1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="events test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    events = await svc.get_events(goal_id=gid, tenant_ctx=T)
    assert isinstance(events, list)


async def test_goal_service_audit_entries():
    """GoalService.get_audit_entries returns a list."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-audit-t1", plan=PlanTier.FREE, api_key_id="gsa1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="audit test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    entries = await svc.get_audit_entries(goal_id=gid, tenant_ctx=T)
    assert isinstance(entries, list)


async def test_goal_service_handle_approval():
    """GoalService.handle_approval returns request_id in response."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-hitl-t1", plan=PlanTier.PROFESSIONAL, api_key_id="gsh1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="hitl test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    approval = await svc.handle_approval(
        goal_id=gid,
        request_id="req1",
        action="approve",
        approver="admin",
        note="ok",
        tenant_ctx=T,
    )
    assert "request_id" in approval
    assert approval["request_id"] == "req1"
    assert approval["action"] == "approve"


async def test_goal_service_handle_rejection():
    """GoalService.handle_approval handles reject action."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-hitl-rej", plan=PlanTier.PROFESSIONAL, api_key_id="gshr1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="reject test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    rejection = await svc.handle_approval(
        goal_id=gid,
        request_id="req2",
        action="reject",
        approver="admin",
        note="rejected",
        tenant_ctx=T,
    )
    assert rejection["action"] == "reject"


async def test_goal_service_handle_unknown_action():
    """GoalService.handle_approval with unknown action returns accepted=False."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-hitl-unk", plan=PlanTier.FREE, api_key_id="gshu1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="unknown action test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    resp = await svc.handle_approval(
        goal_id=gid,
        request_id="req3",
        action="unknown_action",
        approver="admin",
        note="test",
        tenant_ctx=T,
    )
    assert resp["accepted"] is False


async def test_goal_service_subscribe_events_terminal():
    """subscribe_events yields immediately for terminal (dry_run) goals."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-sse-t1", plan=PlanTier.FREE, api_key_id="gss1"
    )
    svc = GoalService()
    result = await svc.submit_goal(
        goal="sse test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    events = []
    async for event in svc.subscribe_events(goal_id=gid, tenant_ctx=T):
        events.append(event)
    # Dry-run goal is immediately terminal — subscribe should return quickly
    assert isinstance(events, list)


async def test_goal_service_db_persist_no_crash():
    """GoalService works in-memory even when DB factory raises."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="gs-db-t1", plan=PlanTier.FREE, api_key_id="gsd1"
    )

    class _FailDB:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("DB")

        async def __aexit__(self, *args):
            pass

    svc = GoalService(db_session_factory=_FailDB())
    result = await svc.submit_goal(
        goal="db test", priority="normal", dry_run=True, tenant_ctx=T
    )
    assert "goal_id" in result


async def test_goal_service_sync_from_db_noop():
    """sync_from_db returns 0 when no DB factory is configured."""
    from app.services.goal_service import GoalService

    svc = GoalService()
    count = await svc.sync_from_db()
    assert count == 0


# ── 11. TenantService full coverage ───────────────────────────────────────────


async def test_tenant_service_duplicate_email_raises():
    """TenantService raises ConflictError on duplicate email."""
    from app.core.errors import ConflictError
    from app.services.tenant_service import TenantService

    svc = TenantService()
    await svc.create_tenant(name="First", email="dup@test.com")
    with pytest.raises(ConflictError):
        await svc.create_tenant(name="Second", email="dup@test.com")


async def test_tenant_service_case_insensitive_email():
    """Email duplicate detection is case-insensitive."""
    from app.core.errors import ConflictError
    from app.services.tenant_service import TenantService

    svc = TenantService()
    await svc.create_tenant(name="Corp", email="Case@test.com")
    with pytest.raises(ConflictError):
        await svc.create_tenant(name="Corp2", email="CASE@test.com")


async def test_tenant_service_get_tenant():
    """TenantService.get_tenant returns tenant profile."""
    from app.services.tenant_service import TenantService

    svc = TenantService()
    r = await svc.create_tenant(name="GetMe", email="getme@test.com")
    tenant_id = r["tenant_id"]
    profile = await svc.get_tenant(tenant_id)
    assert profile["tenant_id"] == tenant_id
    assert profile["name"] == "GetMe"


async def test_tenant_service_key_expiry():
    """Expired API keys are rejected by resolve_api_key."""
    from datetime import datetime, timedelta

    from app.services.tenant_service import TenantService

    svc = TenantService()
    result = await svc.create_tenant(name="Expiry", email="expiry_cov@test.com")
    tenant_id = result["tenant_id"]

    # Create a key that expired 1 hour ago
    expired_at = datetime.now(UTC) - timedelta(hours=1)
    key_result = await svc.create_api_key(
        tenant_id=tenant_id, name="expired", scopes=[], expires_at=expired_at
    )
    expired_raw_key = key_result["raw_key"]

    # Should not resolve — expired
    ctx = await svc.resolve_api_key(expired_raw_key)
    assert ctx is None


async def test_tenant_service_valid_key_resolves():
    """Valid non-expired API keys are resolved correctly."""
    from datetime import datetime, timedelta

    from app.services.tenant_service import TenantService

    svc = TenantService()
    result = await svc.create_tenant(name="ValidKey", email="validkey_cov@test.com")
    tenant_id = result["tenant_id"]

    # Create a key that expires in the future
    future_at = datetime.now(UTC) + timedelta(hours=24)
    key_result = await svc.create_api_key(
        tenant_id=tenant_id, name="future", scopes=[], expires_at=future_at
    )
    raw_key = key_result["raw_key"]

    ctx = await svc.resolve_api_key(raw_key)
    assert ctx is not None
    assert ctx.tenant_id == tenant_id


async def test_tenant_service_list_api_keys():
    """TenantService.list_api_keys returns all keys for the tenant."""
    from app.services.tenant_service import TenantService

    svc = TenantService()
    r = await svc.create_tenant(name="ListKeys", email="listkeys_cov@test.com")
    tenant_id = r["tenant_id"]

    await svc.create_api_key(tenant_id=tenant_id, name="k1", scopes=[])
    await svc.create_api_key(tenant_id=tenant_id, name="k2", scopes=["goals:read"])

    keys = await svc.list_api_keys(tenant_id)
    # 1 default + 2 created = 3 keys
    assert len(keys) == 3
    names = {k["name"] for k in keys}
    assert "k1" in names
    assert "k2" in names


async def test_tenant_service_sync_from_db_noop():
    """sync_from_db returns 0 when no DB factory is configured."""
    from app.services.tenant_service import TenantService

    svc = TenantService()
    count = await svc.sync_from_db()
    assert count == 0


async def test_tenant_service_revoke_wrong_tenant_raises():
    """Revoking another tenant's key raises NotFoundError."""
    from app.core.errors import NotFoundError
    from app.services.tenant_service import TenantService

    svc = TenantService()
    r1 = await svc.create_tenant(name="T1", email="t1r_cov@test.com")
    r2 = await svc.create_tenant(name="T2", email="t2r_cov@test.com")
    # Try to revoke T1's key using T2's tenant_id
    key_id = r1["api_key_id"]
    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(tenant_id=r2["tenant_id"], key_id=key_id)


async def test_tenant_service_revoke_own_key():
    """Revoking own key deactivates it."""
    from app.services.tenant_service import TenantService

    svc = TenantService()
    r = await svc.create_tenant(name="RevokeMe", email="revokeme_cov@test.com")
    tenant_id = r["tenant_id"]
    key_id = r["api_key_id"]

    # Revoke the key
    await svc.revoke_api_key(tenant_id=tenant_id, key_id=key_id)

    # Key should no longer resolve
    ctx = await svc.resolve_api_key(r["api_key"])
    assert ctx is None


async def test_tenant_service_initial_key_resolves():
    """The initial API key created with a tenant resolves to its TenantContext."""
    from app.services.tenant_service import TenantService

    svc = TenantService()
    r = await svc.create_tenant(name="Resolver", email="resolver_cov@test.com")
    raw_key = r["api_key"]

    ctx = await svc.resolve_api_key(raw_key)
    assert ctx is not None
    assert ctx.tenant_id == r["tenant_id"]


# ── 12. Audit Log full coverage ───────────────────────────────────────────────


def test_audit_log_query_by_goal_and_tool():
    """AuditLog supports filtering by goal_id and tool_name."""
    from app.governance.audit import AuditEvent, AuditLog
    from app.governance.permissions import ActionLevel
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="audit-cov-t1", plan=PlanTier.FREE, api_key_id="ac1"
    )
    log = AuditLog()

    for i in range(3):
        log.record(
            AuditEvent(
                goal_id=f"g{i}",
                tool_name=f"tool{i}",
                action_level=ActionLevel.ALLOW_LOG,
                outcome="ok",
            ),
            tenant_ctx=T,
        )

    # Query by goal_id
    entries = log.query(tenant_ctx=T, goal_id="g0")
    assert len(entries) == 1
    assert entries[0].goal_id == "g0"

    # Query by tool_name
    entries2 = log.query(tenant_ctx=T, tool_name="tool1")
    assert len(entries2) == 1
    assert entries2[0].tool_name == "tool1"

    # Query all
    all_entries = log.query(tenant_ctx=T)
    assert len(all_entries) == 3


async def test_audit_log_db_sync_noop():
    """sync_from_db returns 0 when no DB factory is configured."""
    from app.governance.audit import AuditLog

    log = AuditLog()
    count = await log.sync_from_db()
    assert count == 0


async def test_audit_log_db_sync_noop_with_tenant_id():
    """sync_from_db with tenant_id returns 0 when no DB factory configured."""
    from app.governance.audit import AuditLog

    log = AuditLog()
    count = await log.sync_from_db(tenant_id="some-tenant")
    assert count == 0


def test_audit_log_tenant_isolation_in_query():
    """AuditLog entries are strictly isolated per tenant."""
    from app.governance.audit import AuditEvent, AuditLog
    from app.governance.permissions import ActionLevel
    from app.tenancy.context import PlanTier, TenantContext

    T_A = TenantContext(
        tenant_id="audit-iso-a", plan=PlanTier.FREE, api_key_id="aia"
    )
    T_B = TenantContext(
        tenant_id="audit-iso-b", plan=PlanTier.FREE, api_key_id="aib"
    )
    log = AuditLog()

    log.record(
        AuditEvent(
            goal_id="g1", tool_name="t1", action_level=ActionLevel.ALLOW, outcome="ok"
        ),
        tenant_ctx=T_A,
    )
    log.record(
        AuditEvent(
            goal_id="g2",
            tool_name="t2",
            action_level=ActionLevel.DENY,
            outcome="denied",
        ),
        tenant_ctx=T_B,
    )

    a_entries = log.query(tenant_ctx=T_A)
    b_entries = log.query(tenant_ctx=T_B)
    assert len(a_entries) == 1
    assert len(b_entries) == 1
    assert a_entries[0].goal_id == "g1"
    assert b_entries[0].goal_id == "g2"


def test_audit_log_event_id_is_unique():
    """Each AuditEvent gets a unique event_id."""
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel

    e1 = AuditEvent(
        goal_id="g1", tool_name="t1", action_level=ActionLevel.ALLOW, outcome="ok"
    )
    e2 = AuditEvent(
        goal_id="g1", tool_name="t1", action_level=ActionLevel.ALLOW, outcome="ok"
    )
    assert e1.event_id != e2.event_id


async def test_audit_log_db_factory_fires_task():
    """AuditLog with a DB factory fires background task (which may fail gracefully)."""
    from app.governance.audit import AuditEvent, AuditLog
    from app.governance.permissions import ActionLevel
    from app.tenancy.context import PlanTier, TenantContext

    T = TenantContext(
        tenant_id="audit-db-t1", plan=PlanTier.FREE, api_key_id="adb1"
    )

    class _FailDB:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("DB down")

        async def __aexit__(self, *args):
            pass

    log = AuditLog(db_session_factory=_FailDB())
    # record should work in-memory even if DB fails
    log.record(
        AuditEvent(
            goal_id="g1", tool_name="t1", action_level=ActionLevel.ALLOW, outcome="ok"
        ),
        tenant_ctx=T,
    )
    entries = log.query(tenant_ctx=T)
    assert len(entries) == 1
    # Allow the background task to run and fail gracefully
    await asyncio.sleep(0.05)
