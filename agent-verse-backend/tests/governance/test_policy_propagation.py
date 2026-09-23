"""Tests for PolicyEngine cross-replica propagation via Redis pub/sub."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_reload_from_db_replaces_tenant_policies():
    """reload_from_db(tenant_id=X) removes only X's policies and re-loads from DB."""
    engine = PolicyEngine()
    # Simulate two tenants' policies in memory
    engine._policies = [
        Policy(name="p1", action="deny", tool_pattern="rm", tenant_id="tenant-a"),
        Policy(name="p2", action="deny", tool_pattern="drop", tenant_id="tenant-b"),
    ]

    # Mock DB returning one policy for tenant-a (updated).
    # Use MagicMock (not AsyncMock) for mock_db so that mock_db() returns
    # mock_session directly as an async context manager (not a coroutine).
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=AsyncMock(fetchall=lambda: [
        ("p1-updated", "deny", "rm -rf", "tenant-a"),
    ]))
    mock_db.return_value = mock_session

    count = await engine.reload_from_db(mock_db, tenant_id="tenant-a")

    assert count == 1
    # tenant-b's policy must still be present
    tenant_b_policies = [p for p in engine._policies if getattr(p, "tenant_id", "") == "tenant-b"]
    assert len(tenant_b_policies) == 1
    # tenant-a's policy must be the updated one
    tenant_a_policies = [p for p in engine._policies if getattr(p, "tenant_id", "") == "tenant-a"]
    assert len(tenant_a_policies) == 1
    assert tenant_a_policies[0].tool_pattern == "rm -rf"


@pytest.mark.asyncio
async def test_reload_from_db_does_not_drop_concurrently_created_policy():
    """A create_policy() racing an in-flight reload_from_db() for the same
    tenant must not have its policy silently wiped out by the reload.

    reload_from_db() snapshots ``_policies`` (via a wholesale replace) using
    the result of a DB SELECT it awaited *earlier*. If a concurrent
    create_policy() call — mirroring POST /governance/policies — adds a new
    policy straight to ``_policies`` while that SELECT is still in flight,
    the reload's replace must not discard it. PolicyEngine.lock is what
    serializes the two; this test races them the way two requests hitting
    the same tenant's policy engine could (this same reload's own pub/sub
    echo, or a second operator's change, landing while the first reload is
    still reading from the DB).
    """
    engine = PolicyEngine()

    query_started = asyncio.Event()
    proceed = asyncio.Event()

    class _SlowSession:
        async def __aenter__(self) -> "_SlowSession":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def execute(self, *args: object, **kwargs: object) -> AsyncMock:
            query_started.set()
            await proceed.wait()
            # The SELECT ran before the concurrent create's row existed.
            return AsyncMock(fetchall=lambda: [])

    mock_db = MagicMock()
    mock_db.return_value = _SlowSession()

    async def slow_reload() -> None:
        await engine.reload_from_db(mock_db, tenant_id="tenant-a")

    async def concurrent_create() -> None:
        # Mirrors POST /governance/policies: acquire the engine's reload lock,
        # then add the new policy directly to `_policies`.
        await query_started.wait()
        async with engine.lock:
            engine.add_policy(
                Policy(
                    name="deny-refunds",
                    action="deny",
                    denied_tools=["stripe.refund"],
                    tenant_id="tenant-a",
                )
            )

    async def release_query() -> None:
        # Independent of the lock, so the reload's DB call always completes
        # even while concurrent_create is blocked waiting for the lock.
        await query_started.wait()
        await asyncio.sleep(0.02)
        proceed.set()

    await asyncio.gather(slow_reload(), concurrent_create(), release_query())

    ctx = TenantContext(tenant_id="tenant-a", plan=PlanTier.ENTERPRISE, api_key_id="k", roles=())
    result = engine.evaluate("stripe.refund", tenant_ctx=ctx)
    assert result == PolicyResult.DENY, (
        "a policy created concurrently with an in-flight reload_from_db() "
        "for the same tenant was silently dropped by the reload's "
        "stale-snapshot replace"
    )


@pytest.mark.asyncio
async def test_reload_from_db_no_db_returns_zero():
    engine = PolicyEngine()
    count = await engine.reload_from_db(None)
    assert count == 0


@pytest.mark.asyncio
async def test_publish_change_sends_to_redis():
    """publish_change publishes JSON message to policy_changes channel."""
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    await PolicyEngine.publish_change(mock_redis, tenant_id="tenant-x", action="created")

    mock_redis.publish.assert_called_once()
    call_args = mock_redis.publish.call_args
    channel = call_args[0][0]
    payload = json.loads(call_args[0][1])

    assert channel == "policy_changes"
    assert payload["tenant_id"] == "tenant-x"
    assert payload["action"] == "created"


@pytest.mark.asyncio
async def test_publish_change_no_redis_is_noop():
    """publish_change with redis=None does not raise."""
    await PolicyEngine.publish_change(None, tenant_id="t1", action="deleted")  # must not raise


def test_start_policy_subscriber_returns_task():
    """start_policy_subscriber returns an asyncio.Task."""
    from app.governance.policies import start_policy_subscriber

    async def _run():
        task = start_policy_subscriber("redis://localhost:6379/0", PolicyEngine(), None)
        assert isinstance(task, asyncio.Task)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_governance_api_imports_policy_engine():
    """governance.py must import PolicyEngine for publish_change."""
    import inspect

    from app.api import governance
    src = inspect.getsource(governance)
    assert "PolicyEngine" in src or "publish_change" in src, \
        "governance.py must use PolicyEngine.publish_change for cross-replica propagation"
