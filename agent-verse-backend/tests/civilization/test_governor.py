"""Tests for Governor — central authority for the civilization."""
import json
import pytest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from app.civilization.models import Constitution, SpawnDecision, SpawnVerdict
from app.civilization.governor import Governor


# ── helpers ────────────────────────────────────────────────────────────────────


class _noop_ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _FakeSession:
    def __init__(self, rows=None, raise_on=None):
        self.executions = []
        self._rows = rows or []
        self._raise = raise_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def begin(self):
        return _noop_ctx()

    async def execute(self, stmt, params=None):
        if self._raise:
            raise RuntimeError(self._raise)
        self.executions.append((stmt, params))
        return SimpleNamespace(
            fetchall=lambda: list(self._rows),
            fetchone=lambda: self._rows[0] if self._rows else None,
        )


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


def test_governor_is_paused_sync_exception_returns_false():
    mock_redis_sync = MagicMock()
    mock_redis_sync.get = MagicMock(side_effect=RuntimeError("Redis error"))
    g = _make_governor()
    result = g.is_paused_sync(mock_redis_sync)
    assert result is False


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


# ── Additional coverage tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_governor_get_live_metrics_with_db():
    row = (5, 3, 25.50, 8)  # total, concurrent, spent, spawn_rate
    session = _FakeSession(rows=[row])
    g = _make_governor(db_session_factory=lambda: session)
    metrics = await g._get_live_metrics()
    assert metrics["total_agents"] == 5
    assert metrics["concurrent_agents"] == 3
    assert abs(metrics["budget_spent_usd"] - 25.50) < 0.001
    assert metrics["spawn_rate_last_min"] == 8


@pytest.mark.asyncio
async def test_governor_get_live_metrics_db_exception_returns_defaults():
    session = _FakeSession(raise_on="DB query failed")
    g = _make_governor(db_session_factory=lambda: session)
    metrics = await g._get_live_metrics()
    assert metrics["total_agents"] == 0
    assert metrics["budget_spent_usd"] == 0.0


@pytest.mark.asyncio
async def test_governor_get_live_metrics_none_row_values():
    """Handles NULL values from DB row (returns defaults)."""
    row = (None, None, None, None)
    session = _FakeSession(rows=[row])
    g = _make_governor(db_session_factory=lambda: session)
    metrics = await g._get_live_metrics()
    assert metrics["total_agents"] == 0
    assert metrics["budget_spent_usd"] == 0.0


@pytest.mark.asyncio
async def test_governor_find_idle_matching_no_agent_store():
    g = _make_governor(db_session_factory=None)
    result = await g._find_idle_matching("jira", _make_tenant_ctx())
    assert result is None


@pytest.mark.asyncio
async def test_governor_find_idle_matching_no_match():
    mock_store = AsyncMock()
    mock_store.list_async = AsyncMock(return_value=[
        {"agent_id": "a1", "goal_template": "Handle confluence tasks"},
    ])
    g = _make_governor(agent_store=mock_store)
    g._is_idle_member = AsyncMock(return_value=False)
    result = await g._find_idle_matching("jira", _make_tenant_ctx())
    assert result is None


@pytest.mark.asyncio
async def test_governor_find_idle_matching_returns_idle_agent():
    mock_store = AsyncMock()
    mock_store.list_async = AsyncMock(return_value=[
        {"agent_id": "a1", "goal_template": "Handle jira tasks"},
    ])
    g = _make_governor(agent_store=mock_store)
    g._is_idle_member = AsyncMock(return_value=True)
    result = await g._find_idle_matching("jira", _make_tenant_ctx())
    assert result is not None
    assert result["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_governor_find_idle_matching_exception_returns_none():
    mock_store = AsyncMock()
    mock_store.list_async = AsyncMock(side_effect=RuntimeError("Store error"))
    g = _make_governor(agent_store=mock_store)
    result = await g._find_idle_matching("jira", _make_tenant_ctx())
    assert result is None


@pytest.mark.asyncio
async def test_governor_is_idle_member_no_db():
    g = _make_governor(db_session_factory=None)
    result = await g._is_idle_member("a1")
    assert result is False


@pytest.mark.asyncio
async def test_governor_is_idle_member_idle_status():
    session = _FakeSession(rows=[("idle",)])
    g = _make_governor(db_session_factory=lambda: session)
    result = await g._is_idle_member("a1")
    assert result is True


@pytest.mark.asyncio
async def test_governor_is_idle_member_active_status():
    session = _FakeSession(rows=[("active",)])
    g = _make_governor(db_session_factory=lambda: session)
    result = await g._is_idle_member("a1")
    assert result is False


@pytest.mark.asyncio
async def test_governor_is_idle_member_not_found():
    session = _FakeSession(rows=[])  # no rows
    g = _make_governor(db_session_factory=lambda: session)
    result = await g._is_idle_member("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_governor_is_idle_member_exception_returns_false():
    session = _FakeSession(raise_on="DB error")
    g = _make_governor(db_session_factory=lambda: session)
    result = await g._is_idle_member("a1")
    assert result is False


@pytest.mark.asyncio
async def test_governor_spawn_agent_reuses_idle_member():
    """spawn_agent reuses an existing idle member instead of creating new."""
    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
    )
    existing = {"agent_id": "idle-a1", "goal_template": "Handle jira tasks"}
    g = _make_governor()
    g._find_idle_matching = AsyncMock(return_value=existing)

    result = await g.spawn_agent(
        verdict=approved,
        requested_capability="jira",
        goal_text="search bugs",
        requester_agent_id="parent-a",
        depth=1,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert result["agent_id"] == "idle-a1"
    g._find_idle_matching.assert_called_once()


@pytest.mark.asyncio
async def test_governor_spawn_agent_with_agent_store():
    """spawn_agent uses agent_store.create when no idle match and store provided."""
    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=[],
    )
    mock_store = AsyncMock()
    mock_store.create = AsyncMock(return_value="new-agent-from-store")

    g = _make_governor(agent_store=mock_store)
    g._find_idle_matching = AsyncMock(return_value=None)
    g._register_civilization_member = AsyncMock()

    record = await g.spawn_agent(
        verdict=approved,
        requested_capability="jira",
        goal_text="search bugs",
        requester_agent_id="a1",
        depth=1,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert record["agent_id"] == "new-agent-from-store"
    mock_store.create.assert_called_once()


@pytest.mark.asyncio
async def test_governor_plan_and_validate_with_planner():
    """_plan_and_validate_agent uses MetaAgentPlanner output."""
    mock_config = MagicMock()
    mock_config.name = "JiraAgent"
    mock_config.goal_template = "Handle jira tasks"
    mock_config.connectors = ["jira-conn"]

    mock_planner = AsyncMock()
    mock_planner.plan = AsyncMock(return_value=mock_config)

    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
        clamped_autonomy="supervised",
        inherited_policy_ids=["p1"],
    )

    g = _make_governor(meta_agent_planner=mock_planner)
    result = await g._plan_and_validate_agent(
        requested_capability="jira",
        goal_text="search bugs",
        verdict=approved,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert result.name == "JiraAgent"
    assert result.autonomy_mode == "supervised"
    assert "p1" in result.policy_ids


@pytest.mark.asyncio
async def test_governor_plan_and_validate_fallback_when_planner_fails():
    """_plan_and_validate_agent falls back to minimal config on planner exception."""
    mock_planner = AsyncMock()
    mock_planner.plan = AsyncMock(side_effect=RuntimeError("Planner unavailable"))

    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=[],
    )

    g = _make_governor(meta_agent_planner=mock_planner)
    result = await g._plan_and_validate_agent(
        requested_capability="confluence",
        goal_text="write docs",
        verdict=approved,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert "confluence" in result.name
    assert result.autonomy_mode == "bounded-autonomous"


@pytest.mark.asyncio
async def test_governor_plan_and_validate_fallback_when_no_planner():
    """Without planner, falls back to minimal config."""
    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=["p2"],
    )
    g = _make_governor()
    result = await g._plan_and_validate_agent(
        requested_capability="slack",
        goal_text="send message",
        verdict=approved,
        tenant_ctx=_make_tenant_ctx(),
    )
    assert "slack" in result.name.lower()
    assert result.policy_ids == ["p2"]


@pytest.mark.asyncio
async def test_governor_register_civilization_member_with_db():
    session = _FakeSession()
    g = _make_governor(db_session_factory=lambda: session)
    await g._register_civilization_member(
        agent_id="a1",
        parent_agent_id="parent-a",
        depth=1,
        budget_usd=5.0,
    )
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_governor_register_civilization_member_no_db():
    g = _make_governor(db_session_factory=None)
    # Should not raise
    await g._register_civilization_member(
        agent_id="a1", parent_agent_id="p", depth=0, budget_usd=5.0
    )


@pytest.mark.asyncio
async def test_governor_register_member_db_exception_is_swallowed():
    session = _FakeSession(raise_on="DB insert failed")
    g = _make_governor(db_session_factory=lambda: session)
    await g._register_civilization_member(
        agent_id="a1", parent_agent_id="p", depth=0, budget_usd=5.0
    )


@pytest.mark.asyncio
async def test_governor_retire_member_with_db():
    session = _FakeSession()
    g = _make_governor(db_session_factory=lambda: session)
    await g._retire_member("member-id", "agent-id")
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_governor_retire_member_no_db():
    g = _make_governor(db_session_factory=None)
    await g._retire_member("m1", "a1")  # no-op, should not raise


@pytest.mark.asyncio
async def test_governor_retire_member_db_exception_swallowed():
    session = _FakeSession(raise_on="DB update failed")
    g = _make_governor(db_session_factory=lambda: session)
    await g._retire_member("m1", "a1")  # should not raise


@pytest.mark.asyncio
async def test_governor_retire_member_by_agent_id_with_db():
    session = _FakeSession()
    g = _make_governor(db_session_factory=lambda: session)
    await g._retire_member_by_agent_id("agent-1")
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_governor_retire_member_by_agent_id_no_db():
    g = _make_governor(db_session_factory=None)
    await g._retire_member_by_agent_id("agent-1")  # no-op


@pytest.mark.asyncio
async def test_governor_retire_member_by_agent_id_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    g = _make_governor(db_session_factory=lambda: session)
    await g._retire_member_by_agent_id("agent-1")  # should not raise


@pytest.mark.asyncio
async def test_governor_set_civilization_status_with_db():
    session = _FakeSession()
    g = _make_governor(db_session_factory=lambda: session)
    await g._set_civilization_status("paused")
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_governor_set_civilization_status_no_db():
    g = _make_governor(db_session_factory=None)
    await g._set_civilization_status("active")  # no-op


@pytest.mark.asyncio
async def test_governor_set_civilization_status_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    g = _make_governor(db_session_factory=lambda: session)
    await g._set_civilization_status("paused")  # should not raise


@pytest.mark.asyncio
async def test_governor_kill_agent_sets_redis_key():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    g = _make_governor(redis=mock_redis)
    g._retire_member_by_agent_id = AsyncMock()

    await g.kill_agent("agent-1", _make_tenant_ctx())

    mock_redis.set.assert_called_once()
    call_args = str(mock_redis.set.call_args)
    assert "civ_kill_agent" in call_args
    assert "agent-1" in call_args


@pytest.mark.asyncio
async def test_governor_kill_agent_no_redis():
    g = _make_governor(redis=None)
    g._retire_member_by_agent_id = AsyncMock()
    await g.kill_agent("agent-1", _make_tenant_ctx())
    g._retire_member_by_agent_id.assert_called_once_with("agent-1")


@pytest.mark.asyncio
async def test_governor_kill_agent_redis_exception_swallowed():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=RuntimeError("Redis error"))
    g = _make_governor(redis=mock_redis)
    g._retire_member_by_agent_id = AsyncMock()
    await g.kill_agent("agent-1", _make_tenant_ctx())  # should not raise


@pytest.mark.asyncio
async def test_governor_pause_redis_exception_swallowed():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=RuntimeError("Redis down"))
    g = _make_governor(redis=mock_redis)
    g._set_civilization_status = AsyncMock()
    await g.pause()  # should not raise


@pytest.mark.asyncio
async def test_governor_resume_redis_exception_swallowed():
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(side_effect=RuntimeError("Redis down"))
    g = _make_governor(redis=mock_redis)
    g._set_civilization_status = AsyncMock()
    await g.resume()  # should not raise


@pytest.mark.asyncio
async def test_governor_pause_emits_civilization_paused_event():
    g = _make_governor()
    g._set_civilization_status = AsyncMock()
    with patch("app.civilization.events.emit_event", new_callable=AsyncMock) as mock_emit:
        await g.pause()
    # Should attempt to emit CIVILIZATION_PAUSED
    mock_emit.assert_called()


@pytest.mark.asyncio
async def test_governor_resume_emits_civilization_resumed_event():
    g = _make_governor()
    g._set_civilization_status = AsyncMock()
    with patch("app.civilization.events.emit_event", new_callable=AsyncMock) as mock_emit:
        await g.resume()
    mock_emit.assert_called()


@pytest.mark.asyncio
async def test_governor_auto_pause_calls_hitl_on_breach():
    """_auto_pause calls HITL gateway when configured."""
    mock_hitl = AsyncMock()
    mock_hitl.request_approval = AsyncMock()
    g = _make_governor(hitl_gateway=mock_hitl)
    g.pause = AsyncMock()
    await g._auto_pause(reasons=["budget exhausted"])
    mock_hitl.request_approval.assert_called_once()


@pytest.mark.asyncio
async def test_governor_auto_pause_hitl_exception_is_swallowed():
    mock_hitl = AsyncMock()
    mock_hitl.request_approval = AsyncMock(side_effect=RuntimeError("HITL down"))
    g = _make_governor(hitl_gateway=mock_hitl)
    g.pause = AsyncMock()
    await g._auto_pause(reasons=["spawn rate exceeded"])  # should not raise


@pytest.mark.asyncio
async def test_governor_auto_retire_idle_by_ttl():
    """Members past idle TTL should be retired."""
    from datetime import timedelta
    old_time = datetime(2020, 1, 1, tzinfo=UTC)  # far in the past
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=AsyncMock(fetchall=lambda: [
        ("m1", "agent-1", 0.9, old_time),  # healthy rep but idle too long
        ("m2", "agent-2", 0.9, datetime.now(UTC)),  # recently active
    ]))
    mock_db = MagicMock(return_value=mock_session)

    g = _make_governor(
        db_session_factory=mock_db,
        constitution=Constitution(
            max_depth=3, max_total_agents=10, total_budget_usd=50.0,
            idle_ttl_seconds=3600, min_viable_roster=1,
        ),
    )
    g._retire_member = AsyncMock()
    retired = await g.auto_retire_idle()
    assert "agent-1" in retired


@pytest.mark.asyncio
async def test_governor_auto_retire_exception_returns_empty():
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_db = MagicMock(return_value=mock_session)

    g = _make_governor(db_session_factory=mock_db)
    retired = await g.auto_retire_idle()
    assert retired == []


@pytest.mark.asyncio
async def test_governor_audit_spawn_with_db():
    """_audit_spawn persists to spawn_requests table."""
    session = _FakeSession()
    g = _make_governor(db_session_factory=lambda: session)

    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="within limits",
        allowed_budget_usd=5.0,
    )
    await g._audit_spawn(
        requester_agent_id="a1",
        requested_capability="jira",
        goal_text="search bugs",
        verdict=approved,
    )
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_governor_audit_spawn_db_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    g = _make_governor(db_session_factory=lambda: session)

    denied = SpawnVerdict(
        decision=SpawnDecision.DENIED,
        reason="depth exceeded",
    )
    await g._audit_spawn(
        requester_agent_id="a1",
        requested_capability="jira",
        goal_text="x",
        verdict=denied,
    )


@pytest.mark.asyncio
async def test_governor_audit_spawn_no_db_with_audit_log():
    """Without DB, audit_log.record() is still called."""
    mock_audit = MagicMock()

    g = _make_governor(db_session_factory=None, audit_log=mock_audit)

    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
    )
    await g._audit_spawn(
        requester_agent_id="a1",
        requested_capability="jira",
        goal_text="x",
        verdict=approved,
    )
    mock_audit.record.assert_called_once()


@pytest.mark.asyncio
async def test_governor_audit_spawn_with_db_and_audit_log():
    """With DB, audit_log.record() is also called for compliance trail."""
    session = _FakeSession()
    mock_audit = MagicMock()

    g = _make_governor(db_session_factory=lambda: session, audit_log=mock_audit)

    approved = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="ok",
        allowed_budget_usd=5.0,
    )
    await g._audit_spawn(
        requester_agent_id="a1",
        requested_capability="confluence",
        goal_text="write docs",
        verdict=approved,
    )
    mock_audit.record.assert_called_once()


@pytest.mark.asyncio
async def test_governor_auto_retire_idle_no_db():
    g = _make_governor(db_session_factory=None)
    retired = await g.auto_retire_idle()
    assert retired == []


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
