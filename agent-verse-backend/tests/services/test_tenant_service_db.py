"""Tests for TenantService DB persistence (no-op when db_session_factory=None)."""
from __future__ import annotations


import pytest

from app.core.errors import NotFoundError
from app.services.tenant_service import TenantService


async def test_create_tenant_fires_background_db_task():
    """DB persistence is attempted but doesn't break when factory raises in __aenter__."""
    call_log: list[str] = []

    # Must match the async_sessionmaker() interface: sync callable → async context manager
    class _ErrorSession:
        async def __aenter__(self) -> _ErrorSession:
            call_log.append("called")
            raise RuntimeError("DB unavailable")

        async def __aexit__(self, *args: object) -> None:
            pass

    def fake_db_factory() -> _ErrorSession:
        return _ErrorSession()

    # With a DB configured, a failed durable write fails the signup: a tenant
    # that exists only in one pod's memory cannot authenticate anywhere else.
    import pytest

    from app.services.tenant_service import KeyStoreUnavailableError

    svc = TenantService(db_session_factory=fake_db_factory)
    with pytest.raises(KeyStoreUnavailableError):
        await svc.create_tenant(name="Test Corp", email="test@corp.com")
    assert call_log == ["called"]


async def test_create_tenant_persists_tenant_and_default_key_before_return():
    persisted: list[str] = []

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def begin(self) -> _Session:
            return self

        async def execute(self, *args: object, **kwargs: object) -> None:
            return None

        def add(self, obj: object) -> None:
            persisted.append(type(obj).__name__)

    def fake_db_factory() -> _Session:
        return _Session()

    svc = TenantService(db_session_factory=fake_db_factory)
    await svc.create_tenant(name="Persisted", email="persisted@example.com")

    assert persisted == ["Tenant", "ApiKey"]






async def test_goal_service_sync_from_db_noop():
    from app.services.goal_service import GoalService

    svc = GoalService()
    count = await svc.sync_from_db()
    assert count == 0


async def test_dynamic_resolver_uses_current_app_state():
    """The dynamic resolver reads from app.state, not a captured closure."""
    from app.main import create_app
    from app.services.tenant_service import TenantService

    app = create_app()
    # Replace tenant service after app creation
    new_svc = TenantService()
    result = await new_svc.create_tenant(name="New Tenant", email="new@test.com")
    new_key = result["api_key"]
    app.state.tenant_service = new_svc

    # The dynamic resolver should find the new key via the replaced service
    ctx = await app.state.tenant_service.resolve_api_key(new_key)
    assert ctx is not None
    assert ctx.tenant_id == result["tenant_id"]


async def test_tenant_service_default_has_no_db():
    """TenantService() without args has self._db == None."""
    svc = TenantService()
    assert svc._db is None


async def test_tenant_service_accepts_db_factory():
    """TenantService(db_session_factory=...) stores the factory."""

    async def dummy_factory():
        pass  # pragma: no cover

    svc = TenantService(db_session_factory=dummy_factory)
    assert svc._db is dummy_factory


async def test_revoke_api_key_noop_db():
    """revoke_api_key still works (raises on bad key) with no DB factory."""
    svc = TenantService()
    created = await svc.create_tenant(name="RevokeTest", email="revoke_db@example.com")
    tid = created["tenant_id"]
    key_result = await svc.create_api_key(tenant_id=tid, name="K", scopes=[])
    kid = key_result["key_id"]
    await svc.revoke_api_key(tenant_id=tid, key_id=kid)
    # Key should be inactive in memory
    assert svc._keys[kid]["is_active"] is False

    # Revoking a non-existent key raises NotFoundError
    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(tenant_id=tid, key_id="ghost")


async def test_goal_service_db_persist_goal_noop():
    """_db_persist_goal is a no-op when DB is None."""
    from app.services.goal_service import GoalService

    svc = GoalService()
    # Should complete without error
    await svc._db_persist_goal("gid", "tid", "test goal", "planning", "normal", False)


async def test_goal_service_db_update_goal_status_noop():
    """_db_update_goal_status is a no-op when DB is None."""
    from app.services.goal_service import GoalService

    svc = GoalService()
    await svc._db_update_goal_status("gid", "tid", "complete")


async def test_goal_service_db_persist_step_noop():
    """_db_persist_step is a no-op when DB is None."""
    from app.services.goal_service import GoalService

    svc = GoalService()
    await svc._db_persist_step("gid", "tid", 0, "do thing", "complete", "done")
