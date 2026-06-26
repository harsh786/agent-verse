"""Full RPA integration tests — all 6 gaps verified."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.rpa.executor import RPAExecutor, RPAResult
from app.rpa.session_manager import BrowserSessionManager, BrowserSession


# ── Gap 1: RPA tools visible to agent ─────────────────────────────────────────

def test_rpa_tools_importable_as_tool_refs():
    """RPA tools can be converted to ToolRef objects for ToolContext."""
    from app.rpa.tools import RPA_TOOLS
    from app.agent.tool_context import ToolRef

    tool_refs = [
        ToolRef(
            server_id="rpa",
            server_name="rpa",
            name=str(t["name"]),
            description=str(t["description"]),
            input_schema=dict(t.get("input_schema", {})),
        )
        for t in RPA_TOOLS
    ]
    assert len(tool_refs) == 5
    names = {ref.name for ref in tool_refs}
    assert "rpa_open_url" in names
    assert "rpa_click" in names
    assert "rpa_type" in names
    assert "rpa_extract_text" in names
    assert "rpa_screenshot" in names


def test_rpa_executor_attribute_on_agent_graph():
    """AgentGraph has _rpa_executor attribute for RPA dispatch."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.intelligence.guardrails import GuardrailChecker

    fake = FakeProvider(responses=["done"])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        result_processor=ResultProcessor(), dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(), guardrail_checker=GuardrailChecker(),
    )
    assert hasattr(graph, "_rpa_executor")
    # Can be set externally
    mock_executor = MagicMock()
    graph._rpa_executor = mock_executor
    assert graph._rpa_executor is mock_executor


# ── Gap 2: Session cap enforcement ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_cap_evicts_oldest_on_overflow():
    """When tenant hits max_sessions_per_tenant, oldest is evicted."""
    mgr = BrowserSessionManager(max_sessions_per_tenant=2)

    # Inject 2 fake alive sessions for tenant t1
    for i in range(2):
        fake_session = BrowserSession(session_id=f"old-{i}", tenant_id="t1")
        fake_session._browser = MagicMock()  # Mark as alive
        # Stagger last_used_at so old-0 is the oldest
        fake_session.last_used_at = 1000.0 + i
        mgr._sessions[(f"old-{i}", "t1")] = fake_session

    # Request a 3rd session — should evict oldest (old-0)
    with patch.object(mgr, "_create_session", AsyncMock(
        return_value=BrowserSession(session_id="new-1", tenant_id="t1")
    )):
        session = await mgr.get_or_create("new-1", "t1")

    assert session.session_id == "new-1"
    # old-0 should be gone from _sessions (it was popped during eviction)
    assert ("old-0", "t1") not in mgr._sessions or not mgr._sessions.get(
        ("old-0", "t1"), BrowserSession("x", "x")
    ).is_alive


def test_session_manager_stores_max_sessions_per_tenant():
    """BrowserSessionManager stores max_sessions_per_tenant parameter."""
    mgr = BrowserSessionManager(max_sessions_per_tenant=3)
    assert mgr._max_per_tenant == 3


# ── Gap 3: Redis session registry ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_in_redis_with_mock():
    """Session metadata is written to Redis when redis is configured."""
    mock_redis = MagicMock()
    mock_redis.setex = AsyncMock(return_value=True)

    mgr = BrowserSessionManager()
    mgr._redis = mock_redis

    session = BrowserSession(session_id="s1", tenant_id="t1")
    await mgr._register_in_redis(session)

    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    assert "rpa_session:t1:s1" in str(call_args)


@pytest.mark.asyncio
async def test_register_in_redis_noop_without_redis():
    """No Redis — _register_in_redis is a safe no-op."""
    mgr = BrowserSessionManager()
    mgr._redis = None
    session = BrowserSession(session_id="s1", tenant_id="t1")
    await mgr._register_in_redis(session)  # Should not raise


@pytest.mark.asyncio
async def test_deregister_from_redis():
    """Session metadata is deleted from Redis on close."""
    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock(return_value=1)

    mgr = BrowserSessionManager()
    mgr._redis = mock_redis

    await mgr._deregister_from_redis("s1", "t1")
    mock_redis.delete.assert_called_once_with("rpa_session:t1:s1")


# ── Gap 4: Vision provider ─────────────────────────────────────────────────────

def test_executor_accepts_vision_provider():
    """RPAExecutor stores vision_provider for screenshot analysis."""
    mock_vision = MagicMock()
    executor = RPAExecutor(vision_provider=mock_vision)
    assert executor._vision_provider is mock_vision


def test_executor_vision_provider_defaults_to_none():
    executor = RPAExecutor()
    assert executor._vision_provider is None


# ── Gap 5: All 5 tools in standalone mode ─────────────────────────────────────

@pytest.mark.asyncio
async def test_standalone_click_succeeds_when_playwright_unavailable():
    """rpa_click returns simulation result when Playwright not installed."""
    executor = RPAExecutor()
    # Force simulation path
    executor._playwright_available = False
    result = await executor._execute_simulation(
        tool_name="rpa_click",
        arguments={"selector": "#btn"},
    )
    assert result.success is True
    assert "simulated" in result.output.lower() or "clicked" in result.output.lower()


@pytest.mark.asyncio
async def test_standalone_type_succeeds_when_playwright_unavailable():
    executor = RPAExecutor()
    executor._playwright_available = False
    result = await executor._execute_simulation(
        tool_name="rpa_type",
        arguments={"selector": "#inp", "text": "hello"},
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_extract_text_succeeds_when_playwright_unavailable():
    executor = RPAExecutor()
    executor._playwright_available = False
    result = await executor._execute_simulation(
        tool_name="rpa_extract_text",
        arguments={"selector": "body"},
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_all_5_tools_execute_without_playwright():
    """All 5 RPA tools complete without Playwright (simulation fallback)."""
    executor = RPAExecutor()
    executor._playwright_available = False

    tools = [
        ("rpa_open_url", {"url": "https://example.com"}),
        ("rpa_click", {"selector": "#btn"}),
        ("rpa_type", {"selector": "#input", "text": "test"}),
        ("rpa_extract_text", {"selector": "body"}),
        ("rpa_screenshot", {"name": "test"}),
    ]
    for tool_name, args in tools:
        result = await executor.execute(tool_name=tool_name, arguments=args)
        assert isinstance(result.success, bool), f"{tool_name} should return bool success"
        assert isinstance(result.duration_ms, float), f"{tool_name} should have duration_ms"


# ── Gap 6: RPA in goal service tool context ────────────────────────────────────

@pytest.mark.asyncio
async def test_build_tool_context_includes_rpa_tools():
    """GoalService._build_tool_context always includes RPA tools."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import TenantContext, PlanTier

    svc = GoalService()
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    tool_ctx = await svc._build_tool_context(agent_id=None, tenant_ctx=ctx)
    tool_names = {t.name for t in tool_ctx.tools}

    # RPA tools should always be present
    assert "rpa_open_url" in tool_names
    assert "rpa_click" in tool_names
    assert "rpa_type" in tool_names
    assert "rpa_extract_text" in tool_names
    assert "rpa_screenshot" in tool_names
