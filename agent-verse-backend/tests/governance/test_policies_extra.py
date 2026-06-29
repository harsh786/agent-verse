"""Extra coverage tests for app/governance/policies.py — PolicyVersionManager paths."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.policies import (
    REGULATED_DOMAINS,
    Policy,
    PolicyEngine,
    PolicyResult,
    PolicyVersionManager,
    evaluate_with_domain_failsafe,
    start_policy_subscriber,
)
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


# ---------------------------------------------------------------------------
# evaluate_with_domain_failsafe
# ---------------------------------------------------------------------------

def test_domain_failsafe_regulated():
    assert evaluate_with_domain_failsafe("read_phi", "healthcare") == PolicyResult.REQUIRE_APPROVAL
    assert evaluate_with_domain_failsafe("send_data", "hipaa") == PolicyResult.REQUIRE_APPROVAL
    assert evaluate_with_domain_failsafe("file_tax", "finance") == PolicyResult.REQUIRE_APPROVAL
    assert evaluate_with_domain_failsafe("draft_contract", "legal") == PolicyResult.REQUIRE_APPROVAL
    assert evaluate_with_domain_failsafe("charge_card", "pci") == PolicyResult.REQUIRE_APPROVAL


def test_domain_failsafe_unregulated():
    assert evaluate_with_domain_failsafe("send_email", "marketing") == PolicyResult.ALLOW
    assert evaluate_with_domain_failsafe("create_ticket", None) == PolicyResult.ALLOW
    assert evaluate_with_domain_failsafe("search_web") == PolicyResult.ALLOW


def test_regulated_domains_constant():
    assert "healthcare" in REGULATED_DOMAINS
    assert "fintech" in REGULATED_DOMAINS
    assert "sox" in REGULATED_DOMAINS


# ---------------------------------------------------------------------------
# PolicyEngine — time window / weekday checks
# ---------------------------------------------------------------------------

def test_policy_within_time_window_no_restriction():
    p = Policy(name="open", allowed_hours_utc=None, allowed_weekdays=None)
    engine = PolicyEngine([p])
    assert engine._is_within_time_window(p) is True


def test_policy_time_window_always_outside():
    """Hour outside the allowed window returns False."""
    p = Policy(name="restricted", allowed_hours_utc=(9, 17))
    engine = PolicyEngine([p])
    # Use an impossible hour range to guarantee False
    p2 = Policy(name="impossible", allowed_hours_utc=(25, 26))  # hours 25-26 never exist
    # The check will always fail for hour 25-26 since current hour is 0-23
    assert engine._is_within_time_window(p2) is False


def test_policy_weekday_restriction():
    """Empty allowed_weekdays list: no weekday is allowed."""
    p = Policy(name="restricted-weekdays", allowed_weekdays=[])
    engine = PolicyEngine([p])
    # Empty list means no weekday is in the list, so it always returns False
    result = engine._is_within_time_window(p)
    assert result is False


def test_policy_invalid_timezone_falls_back_to_utc():
    p = Policy(name="bad-tz", timezone="Invalid/Timezone", allowed_hours_utc=(0, 23))
    engine = PolicyEngine([p])
    # Should not raise — falls back to UTC
    result = engine._is_within_time_window(p)
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate — inherited parent policies
# ---------------------------------------------------------------------------

def test_evaluate_inherits_parent_policy():
    parent_policy = Policy(name="parent-deny", denied_tools=["delete_*"], tenant_id="t1")
    parent_policy.policy_id = "parent-policy-id"  # type: ignore[attr-defined]
    engine = PolicyEngine([parent_policy])
    ctx = _ctx("t2")  # Different tenant
    # Without parent inheritance, should allow
    result_no_inherit = engine.evaluate("delete_all", tenant_ctx=ctx)
    assert result_no_inherit == PolicyResult.ALLOW

    # With parent_policy_ids referencing the parent
    result_inherit = engine.evaluate(
        "delete_all",
        tenant_ctx=ctx,
        parent_policy_ids=["parent-policy-id"],
    )
    assert result_inherit == PolicyResult.DENY


# ---------------------------------------------------------------------------
# PolicyEngine.reload_from_db
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reload_from_db_no_db():
    engine = PolicyEngine()
    count = await engine.reload_from_db(None)
    assert count == 0


@pytest.mark.asyncio
async def test_reload_from_db_all_tenants():
    from contextlib import asynccontextmanager

    mock_rows = [
        ("deny-all", "deny", "search_*", "tid-1"),
        ("approve-deploy", "require_approval", "deploy_*", "tid-2"),
        ("unknown-action", "allow", "read_*", "tid-3"),
    ]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=mock_rows)))

    @asynccontextmanager
    async def _db():
        yield mock_session

    engine = PolicyEngine()
    count = await engine.reload_from_db(_db)
    assert count == 3
    assert len(engine._policies) == 3


@pytest.mark.asyncio
async def test_reload_from_db_tenant_specific():
    from contextlib import asynccontextmanager

    mock_rows = [("deny-search", "deny", "search_*", "tid-1")]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=mock_rows)))

    @asynccontextmanager
    async def _db():
        yield mock_session

    engine = PolicyEngine()
    # Pre-add a policy for tid-1
    engine.add_policy(Policy(name="old", denied_tools=["old_*"], tenant_id="tid-1"))
    count = await engine.reload_from_db(_db, tenant_id="tid-1")
    assert count == 1
    # Old policy should be replaced
    assert all(p.name == "deny-search" for p in engine._policies)


@pytest.mark.asyncio
async def test_reload_from_db_exception_returns_zero():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _db():
        raise RuntimeError("DB error")
        yield  # pragma: no cover

    engine = PolicyEngine()
    count = await engine.reload_from_db(_db)
    assert count == 0


# ---------------------------------------------------------------------------
# PolicyEngine.publish_change
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_change_with_redis():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()
    await PolicyEngine.publish_change(mock_redis, "tid-1", "created")
    mock_redis.publish.assert_called_once()
    call_args = mock_redis.publish.call_args[0]
    assert call_args[0] == "policy_changes"
    import json
    data = json.loads(call_args[1])
    assert data["tenant_id"] == "tid-1"
    assert data["action"] == "created"


@pytest.mark.asyncio
async def test_publish_change_no_redis():
    # Should be no-op without raising
    await PolicyEngine.publish_change(None, "tid-1", "deleted")


@pytest.mark.asyncio
async def test_publish_change_redis_exception():
    mock_redis = AsyncMock()
    mock_redis.publish.side_effect = RuntimeError("Redis error")
    # Should not raise
    await PolicyEngine.publish_change(mock_redis, "tid-1", "created")


# ---------------------------------------------------------------------------
# PolicyVersionManager.create_policy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_version_manager_create_policy():
    mock_pv = MagicMock()
    mock_pv.id = "pv-uuid-1"

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    with patch(
        "app.db.models.governance.PolicyVersion",
        return_value=mock_pv,
    ):
        pvm = PolicyVersionManager()
        result = await pvm.create_policy(
            db=mock_db,
            tenant_id="tid-1",
            name="deny-risky",
            rules=[{"tool": "delete_*", "action": "deny"}],
            description="Deny risky tools",
            change_summary="Initial",
            changed_by="admin@example.com",
        )
    assert result["version_number"] == 1
    assert result["name"] == "deny-risky"
    assert result["is_active"] is True
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_policy_version_manager_create_policy_with_parent():
    mock_pv = MagicMock()
    mock_pv.id = "pv-uuid-2"

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    with patch(
        "app.db.models.governance.PolicyVersion",
        return_value=mock_pv,
    ):
        pvm = PolicyVersionManager()
        result = await pvm.create_policy(
            db=mock_db,
            tenant_id="tid-1",
            name="child-policy",
            rules=[],
            parent_policy_id="parent-pol-id",
        )
    assert result["version_number"] == 1


# ---------------------------------------------------------------------------
# PolicyVersionManager.update_policy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_version_manager_update_policy():
    from app.db.models.governance import PolicyVersion

    mock_current_pv = MagicMock(spec=PolicyVersion)
    mock_current_pv.id = "cur-id"
    mock_current_pv.version_number = 3
    mock_current_pv.name = "old-name"
    mock_current_pv.rules = [{"tool": "read_*"}]
    mock_current_pv.description = "Old desc"
    mock_current_pv.parent_policy_id = None

    call_count = [0]

    async def _execute(stmt, params=None):
        call_count[0] += 1
        mock_result = MagicMock()
        if call_count[0] == 1:
            mock_result.scalar_one_or_none.return_value = mock_current_pv
        else:
            mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    pvm = PolicyVersionManager()
    result = await pvm.update_policy(
        db=mock_db,
        tenant_id="tid-1",
        policy_id="pol-123",
        updates={"name": "new-name"},
        change_summary="Updated name",
        changed_by="admin",
    )
    assert result["version_number"] == 4
    assert result["name"] == "new-name"


@pytest.mark.asyncio
async def test_policy_version_manager_update_policy_not_found():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    pvm = PolicyVersionManager()
    with pytest.raises(ValueError, match="not found"):
        await pvm.update_policy(
            db=mock_db,
            tenant_id="tid-1",
            policy_id="ghost-pol",
            updates={},
            change_summary="x",
        )


# ---------------------------------------------------------------------------
# PolicyVersionManager.rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_version_manager_rollback():
    mock_target = MagicMock()
    mock_target.version_number = 2
    mock_target.name = "v2-policy"
    mock_target.rules = [{"tool": "search_*"}]
    mock_target.description = "Version 2"

    call_count = [0]

    async def _execute(stmt, params=None):
        call_count[0] += 1
        mock_result = MagicMock()
        if call_count[0] == 1:
            # First call: find target version
            mock_result.scalar_one_or_none.return_value = mock_target
        elif call_count[0] == 3:
            # Third call: max version number
            mock_result.scalar.return_value = 5
        else:
            mock_result.scalar.return_value = None
            mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=_execute)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    pvm = PolicyVersionManager()
    result = await pvm.rollback(
        db=mock_db,
        tenant_id="tid-1",
        policy_id="pol-rollback",
        target_version=2,
        reason="Emergency rollback",
        rolled_back_by="sre@example.com",
    )
    assert result["version_number"] == 6  # max_ver(5) + 1
    assert result["name"] == "v2-policy"


@pytest.mark.asyncio
async def test_policy_version_manager_rollback_not_found():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    pvm = PolicyVersionManager()
    with pytest.raises(ValueError, match="not found"):
        await pvm.rollback(
            db=mock_db,
            tenant_id="tid-1",
            policy_id="ghost",
            target_version=99,
            reason="test",
        )


# ---------------------------------------------------------------------------
# PolicyVersionManager.get_version_history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_version_manager_get_version_history():
    mock_versions = [MagicMock(version_number=1), MagicMock(version_number=2)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_versions

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    pvm = PolicyVersionManager()
    history = await pvm.get_version_history(
        db=mock_db, tenant_id="tid-1", policy_id="pol-history"
    )
    assert len(history) == 2


# ---------------------------------------------------------------------------
# start_policy_subscriber
# ---------------------------------------------------------------------------

def test_start_policy_subscriber():
    import asyncio

    async def _run():
        engine = PolicyEngine()
        with patch.object(asyncio, "create_task", return_value=MagicMock()) as mock_task:
            task = start_policy_subscriber("redis://localhost:6379", engine, None)
            # create_task was called
            mock_task.assert_called_once()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate — approval_tools path
# ---------------------------------------------------------------------------

def test_evaluate_approval_tools():
    """Matching approval_tools → REQUIRE_APPROVAL."""
    p = Policy(
        name="approve-deploy",
        approval_tools=["deploy_*", "prod_*"],
    )
    engine = PolicyEngine([p])
    ctx = _ctx("t1")
    result = engine.evaluate("deploy_prod", tenant_ctx=ctx)
    assert result == PolicyResult.REQUIRE_APPROVAL


def test_evaluate_approval_tools_no_match():
    """Non-matching approval_tools → ALLOW."""
    p = Policy(
        name="approve-prod",
        approval_tools=["prod_*"],
    )
    engine = PolicyEngine([p])
    ctx = _ctx("t1")
    result = engine.evaluate("search_web", tenant_ctx=ctx)
    assert result == PolicyResult.ALLOW


def test_evaluate_time_window_inactive_skips_policy():
    """Policy with impossible time window is skipped during evaluate."""
    # denied_tools has a match, but the time window is never active (hour 25 never exists)
    p = Policy(
        name="night-deny",
        denied_tools=["delete_*"],
        allowed_hours_utc=(25, 26),  # impossible range
    )
    engine = PolicyEngine([p])
    ctx = _ctx("t1")
    # Should ALLOW because the time window is never active
    result = engine.evaluate("delete_all", tenant_ctx=ctx)
    assert result == PolicyResult.ALLOW


def test_evaluate_time_window_inactive_with_approval_tools():
    """Approval_tools policy skipped when time window is not active."""
    p = Policy(
        name="prod-approval",
        approval_tools=["deploy_*"],
        allowed_hours_utc=(25, 26),  # impossible range
    )
    engine = PolicyEngine([p])
    ctx = _ctx("t1")
    result = engine.evaluate("deploy_prod", tenant_ctx=ctx)
    assert result == PolicyResult.ALLOW  # skipped because of time window


def test_evaluate_multiple_policies_first_deny_wins():
    """With multiple policies, DENY from first applicable policy wins."""
    p1 = Policy(name="allow-all", approval_tools=[])
    p2 = Policy(name="deny-search", denied_tools=["search_*"])
    engine = PolicyEngine([p1, p2])
    ctx = _ctx("t1")
    result = engine.evaluate("search_web", tenant_ctx=ctx)
    assert result == PolicyResult.DENY
