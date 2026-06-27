"""Tests for Governor — central authority for the civilization."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.civilization.models import Constitution, SpawnDecision
from app.civilization.governor import Governor


def _make_governor(**kwargs) -> Governor:
    defaults = dict(
        constitution=Constitution(max_depth=3, max_total_agents=10, total_budget_usd=50.0),
        civilization_id="civ-1",
        tenant_id="t1",
        db_session_factory=None,
        redis=None,
    )
    defaults.update(kwargs)
    return Governor(**defaults)


def _make_tenant_ctx():
    """Build a minimal TenantContext for tests."""
    from app.tenancy.context import TenantContext, PlanTier
    return TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.mark.asyncio
async def test_governor_approve_spawn_within_limits():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 10.0, "spawn_rate_last_min": 3,
    })
    g._audit_spawn = AsyncMock()

    verdict = await g.evaluate_spawn_request(
        requester_agent_id="a1", requested_capability="jira", goal_text="search",
        depth=1, parent_budget_usd=10.0, parent_policy_ids=[], tenant_ctx=_make_tenant_ctx(),
    )
    assert verdict.decision == SpawnDecision.APPROVED


@pytest.mark.asyncio
async def test_governor_deny_spawn_exceeds_depth():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 10.0, "spawn_rate_last_min": 3,
    })
    g._audit_spawn = AsyncMock()

    verdict = await g.evaluate_spawn_request(
        requester_agent_id="a1", requested_capability="jira", goal_text="search",
        depth=3, parent_budget_usd=10.0, parent_policy_ids=[], tenant_ctx=_make_tenant_ctx(),
    )
    assert verdict.decision == SpawnDecision.DENIED


@pytest.mark.asyncio
async def test_governor_deny_spawn_budget_exhausted():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 50.1,  # exceeds 50.0
        "spawn_rate_last_min": 3,
    })
    g._audit_spawn = AsyncMock()

    verdict = await g.evaluate_spawn_request(
        requester_agent_id="a1", requested_capability="jira", goal_text="search",
        depth=1, parent_budget_usd=10.0, parent_policy_ids=[], tenant_ctx=_make_tenant_ctx(),
    )
    assert verdict.decision == SpawnDecision.DENIED


@pytest.mark.asyncio
async def test_governor_audit_spawn_called_on_approve():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 10.0, "spawn_rate_last_min": 3,
    })
    g._audit_spawn = AsyncMock()

    await g.evaluate_spawn_request(
        requester_agent_id="a1", requested_capability="jira", goal_text="search",
        depth=1, parent_budget_usd=10.0, parent_policy_ids=[], tenant_ctx=_make_tenant_ctx(),
    )
    g._audit_spawn.assert_called_once()


@pytest.mark.asyncio
async def test_governor_audit_spawn_called_on_deny():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 10.0, "spawn_rate_last_min": 3,
    })
    g._audit_spawn = AsyncMock()

    await g.evaluate_spawn_request(
        requester_agent_id="a1", requested_capability="jira", goal_text="search",
        depth=3, parent_budget_usd=10.0, parent_policy_ids=[], tenant_ctx=_make_tenant_ctx(),
    )
    # Audit must be called even on denied spawns
    g._audit_spawn.assert_called_once()


@pytest.mark.asyncio
async def test_governor_spawn_agent_raises_on_denied_verdict():
    from app.civilization.models import SpawnVerdict, SpawnDecision
    g = _make_governor()
    denied_verdict = SpawnVerdict(
        decision=SpawnDecision.DENIED,
        reason="depth exceeded",
    )
    with pytest.raises(ValueError, match="Cannot spawn with DENIED verdict"):
        await g.spawn_agent(
            verdict=denied_verdict,
            requested_capability="jira",
            goal_text="search",
            requester_agent_id="a1",
            depth=1,
            tenant_ctx=_make_tenant_ctx(),
        )


@pytest.mark.asyncio
async def test_governor_spawn_agent_creates_record_without_agent_store():
    from app.civilization.models import SpawnVerdict, SpawnDecision
    g = _make_governor()
    g._find_idle_matching = AsyncMock(return_value=None)
    g._register_civilization_member = AsyncMock()

    approved_verdict = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="approved",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=[],
    )

    record = await g.spawn_agent(
        verdict=approved_verdict,
        requested_capability="jira",
        goal_text="search for bugs",
        requester_agent_id="a1",
        depth=1,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert "agent_id" in record
    assert record["autonomy_mode"] == "bounded-autonomous"
    g._register_civilization_member.assert_called_once()


@pytest.mark.asyncio
async def test_governor_auto_retire_below_reputation_floor():
    from datetime import UTC, datetime
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=AsyncMock(fetchall=lambda: [
        ("m1", "agent-1", 0.1, datetime.now(UTC)),  # below floor 0.2
        ("m2", "agent-2", 0.9, datetime.now(UTC)),  # healthy
    ]))
    mock_db = MagicMock(return_value=mock_session)

    g = _make_governor(db_session_factory=mock_db)
    g._retire_member = AsyncMock()

    retired = await g.auto_retire_idle()

    assert "agent-1" in retired
    assert "agent-2" not in retired


@pytest.mark.asyncio
async def test_governor_auto_retire_respects_min_viable_roster():
    """When only min_viable_roster members remain, none should be retired."""
    from datetime import UTC, datetime
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=AsyncMock(fetchall=lambda: [
        ("m1", "agent-1", 0.05, datetime.now(UTC)),  # well below floor, but only member
    ]))
    mock_db = MagicMock(return_value=mock_session)

    g = _make_governor(
        db_session_factory=mock_db,
        constitution=Constitution(
            max_depth=3, max_total_agents=10, total_budget_usd=50.0, min_viable_roster=1
        ),
    )
    g._retire_member = AsyncMock()

    retired = await g.auto_retire_idle()
    # Should not retire the last member
    assert "agent-1" not in retired
    g._retire_member.assert_not_called()


@pytest.mark.asyncio
async def test_governor_pause_sets_redis_flag():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()

    g = _make_governor(redis=mock_redis)
    g._set_civilization_status = AsyncMock()  # isolate: test only the Redis side

    await g.pause()

    mock_redis.set.assert_called_once()
    call_args = str(mock_redis.set.call_args)
    assert "civ_paused" in call_args


@pytest.mark.asyncio
async def test_governor_resume_deletes_redis_flag():
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()

    g = _make_governor(redis=mock_redis)
    g._set_civilization_status = AsyncMock()  # isolate: test only the Redis side

    await g.resume()

    mock_redis.delete.assert_called_once()
    call_args = str(mock_redis.delete.call_args)
    assert "civ_paused" in call_args


def test_governor_is_paused_sync_true():
    mock_redis_sync = MagicMock()
    mock_redis_sync.get = MagicMock(return_value=b"1")
    g = _make_governor()
    assert g.is_paused_sync(mock_redis_sync) is True


def test_governor_is_paused_sync_false():
    mock_redis_sync = MagicMock()
    mock_redis_sync.get = MagicMock(return_value=None)
    g = _make_governor()
    assert g.is_paused_sync(mock_redis_sync) is False


def test_governor_is_paused_sync_no_redis():
    g = _make_governor()
    assert g.is_paused_sync(None) is False


@pytest.mark.asyncio
async def test_governor_check_breach_triggers_pause():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 50.1,  # exceeds 50.0
        "spawn_rate_last_min": 3,
    })
    g.pause = AsyncMock()
    g._hitl = None

    verdict = await g.check_breach()

    assert verdict.breached is True
    g.pause.assert_called_once()


@pytest.mark.asyncio
async def test_governor_check_no_breach_does_not_pause():
    g = _make_governor()
    g._get_live_metrics = AsyncMock(return_value={
        "total_agents": 2, "concurrent_agents": 2,
        "budget_spent_usd": 20.0,
        "spawn_rate_last_min": 3,
    })
    g.pause = AsyncMock()
    g._hitl = None

    verdict = await g.check_breach()

    assert verdict.breached is False
    g.pause.assert_not_called()


@pytest.mark.asyncio
async def test_governor_get_live_metrics_returns_defaults_without_db():
    g = _make_governor(db_session_factory=None)
    metrics = await g._get_live_metrics()
    assert metrics["total_agents"] == 0
    assert metrics["concurrent_agents"] == 0
    assert metrics["budget_spent_usd"] == 0.0
    assert metrics["spawn_rate_last_min"] == 0
