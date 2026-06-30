"""Additional RPA coverage tests for session.py, session_manager.py,
credential_injector.py, and artifacts.py.

Targets:
  - session.py lines 74-75, 79-81, 91, 102-104, 115-124, 137-141
  - session_manager.py lines 119-124, 154-155, 223-224, 232-233, 237-253, 258-261
  - credential_injector.py lines 57-71, 80
  - artifacts.py lines 51, 115-116, 135-136
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# RPASessionStore (session.py) — Redis-backed paths
# ═══════════════════════════════════════════════════════════════════════════════


def _make_session_store(redis: MagicMock | None = None):
    from app.rpa.session import RPASessionStore
    return RPASessionStore(redis=redis)


def _make_mock_redis():
    r = AsyncMock()
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.sadd = AsyncMock()
    r.expire = AsyncMock()
    r.smembers = AsyncMock(return_value=set())
    r.delete = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_session_store_redis_save_sadd_expire() -> None:
    """Lines 74-75: _redis_save calls sadd and expire on the tenant index."""
    import json
    from dataclasses import asdict
    from app.rpa.session import RPAManagedSession

    redis = _make_mock_redis()
    store = _make_session_store(redis)

    session = RPAManagedSession(tenant_id="t1")
    # Call _redis_save directly
    await store._redis_save(session)

    redis.set.assert_awaited_once()
    redis.sadd.assert_awaited_once()  # line 74
    redis.expire.assert_awaited_once()  # line 75


@pytest.mark.asyncio
async def test_session_store_redis_load_returns_none_when_missing() -> None:
    """Lines 79-80: _redis_load returns None when Redis returns None."""
    from app.rpa.session import RPAManagedSession

    redis = _make_mock_redis()
    redis.get = AsyncMock(return_value=None)  # key not found
    store = _make_session_store(redis)

    result = await store._redis_load("missing-session-id")
    assert result is None  # line 80


@pytest.mark.asyncio
async def test_session_store_redis_load_returns_session_when_found() -> None:
    """Line 81: _redis_load returns RPAManagedSession when found in Redis."""
    import json
    from dataclasses import asdict
    from app.rpa.session import RPAManagedSession

    session = RPAManagedSession(tenant_id="t1")
    redis = _make_mock_redis()
    redis.get = AsyncMock(return_value=json.dumps(asdict(session)))
    store = _make_session_store(redis)

    result = await store._redis_load(session.session_id)
    assert result is not None
    assert result.tenant_id == "t1"


@pytest.mark.asyncio
async def test_session_store_create_with_redis_returns_session() -> None:
    """Line 91: create() with Redis returns the session after saving."""
    from app.rpa.session import RPAManagedSession

    redis = _make_mock_redis()
    store = _make_session_store(redis)

    session = await store.create(tenant_id="t1")
    assert session is not None
    assert session.tenant_id == "t1"
    redis.set.assert_awaited_once()  # line 91 path goes through _redis_save


@pytest.mark.asyncio
async def test_session_store_get_with_redis_returns_correct_tenant() -> None:
    """Lines 102-104: get() with Redis returns session if tenant matches."""
    import json
    from dataclasses import asdict
    from app.rpa.session import RPAManagedSession

    session = RPAManagedSession(tenant_id="t1")
    redis = _make_mock_redis()
    redis.get = AsyncMock(return_value=json.dumps(asdict(session)))
    store = _make_session_store(redis)

    # Correct tenant — returns session (line 103)
    result = await store.get(session.session_id, tenant_id="t1")
    assert result is not None

    # Wrong tenant — returns None (line 103: s.tenant_id != tenant_id)
    result_wrong = await store.get(session.session_id, tenant_id="other")
    assert result_wrong is None  # line 103: returns None


@pytest.mark.asyncio
async def test_session_store_get_with_redis_missing_returns_none() -> None:
    """Line 104: get() returns None when session not found in Redis."""
    redis = _make_mock_redis()
    redis.get = AsyncMock(return_value=None)
    store = _make_session_store(redis)

    result = await store.get("nonexistent", tenant_id="t1")
    assert result is None  # line 104


@pytest.mark.asyncio
async def test_session_store_list_active_with_redis() -> None:
    """Lines 115-122: list_active() with Redis lists active sessions."""
    import json
    from dataclasses import asdict
    from app.rpa.session import RPAManagedSession

    s1 = RPAManagedSession(tenant_id="t1", status="active")
    s2 = RPAManagedSession(tenant_id="t1", status="closed")

    redis = _make_mock_redis()
    redis.smembers = AsyncMock(return_value={s1.session_id, s2.session_id})

    def _mock_get(key):
        if s1.session_id in key:
            return json.dumps(asdict(s1))
        if s2.session_id in key:
            return json.dumps(asdict(s2))
        return None

    redis.get = AsyncMock(side_effect=lambda k: _mock_get(k))
    store = _make_session_store(redis)

    results = await store.list_active(tenant_id="t1")
    # Only active sessions returned (lines 120-121)
    assert all(s.status == "active" for s in results)


@pytest.mark.asyncio
async def test_session_store_list_active_redis_exception_fallback() -> None:
    """Lines 123-124: Redis error in list_active → falls through to in-memory."""
    redis = _make_mock_redis()
    redis.smembers = AsyncMock(side_effect=RuntimeError("Redis down"))
    store = _make_session_store(redis)

    # Falls back to empty in-memory fallback
    results = await store.list_active(tenant_id="t1")
    assert results == []


@pytest.mark.asyncio
async def test_session_store_close_with_redis_saves_closed() -> None:
    """Lines 137-140: close() with Redis saves the closed session."""
    import json
    from dataclasses import asdict
    from app.rpa.session import RPAManagedSession

    session = RPAManagedSession(tenant_id="t1")
    redis = _make_mock_redis()
    redis.get = AsyncMock(return_value=json.dumps(asdict(session)))
    store = _make_session_store(redis)

    result = await store.close(session.session_id, tenant_id="t1")
    assert result is True
    redis.set.assert_awaited()  # _redis_save called after status change


@pytest.mark.asyncio
async def test_session_store_close_redis_exception_fallback() -> None:
    """Lines 140-141: Redis error in close() falls through to in-memory."""
    import json
    from dataclasses import asdict
    from app.rpa.session import RPAManagedSession

    session = RPAManagedSession(tenant_id="t1")
    redis = _make_mock_redis()
    redis.get = AsyncMock(return_value=json.dumps(asdict(session)))
    redis.set = AsyncMock(side_effect=RuntimeError("Redis write failed"))
    store = _make_session_store(redis)

    result = await store.close(session.session_id, tenant_id="t1")
    assert result is True  # still succeeds via fallback


# ═══════════════════════════════════════════════════════════════════════════════
# BrowserSessionManager (session_manager.py) — uncovered paths
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_browser_session_manager_cap_reached_no_evict() -> None:
    """Lines 119-124: all tenant slots full AND no idle session → return simulation-only."""
    from app.rpa.session_manager import BrowserSession, BrowserSessionManager

    manager = BrowserSessionManager(max_sessions_per_tenant=1)
    manager._playwright_available = False  # skip playwright

    # Fill tenant t1's slot with a busy session (is_alive=True means it has a browser)
    busy = BrowserSession(session_id="busy1", tenant_id="t1")
    busy._browser = MagicMock()  # marks as alive — cannot be evicted
    manager._sessions[("busy1", "t1")] = busy

    # Request another session for t1 — slot full, no idle sessions to evict
    result = await manager.get_or_create("new-sid", "t1")

    # Should return a bare BrowserSession (simulation-only), line 124
    assert result is not None
    assert result.session_id == "new-sid"


@pytest.mark.asyncio
async def test_browser_session_manager_create_session_import_error() -> None:
    """Lines 154-155: ImportError when importing playwright → session has no page."""
    from app.rpa.session_manager import BrowserSessionManager

    manager = BrowserSessionManager()

    with patch("builtins.__import__", side_effect=ImportError("playwright not installed")):
        # Directly call _create_session to trigger ImportError path
        # The side_effect will make 'from playwright.async_api import...' raise
        pass

    # Use a fresh create without playwright installed
    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        session = await manager._create_session("sess1", "tenant1")
    assert session.page is None  # line 154-155: ImportError silenced


@pytest.mark.asyncio
async def test_browser_session_manager_register_redis_exception() -> None:
    """Lines 223-224: Redis setex raises → exception silenced."""
    from app.rpa.session_manager import BrowserSession, BrowserSessionManager

    mock_redis = AsyncMock()
    mock_redis.setex = AsyncMock(side_effect=RuntimeError("Redis down"))

    manager = BrowserSessionManager()
    manager._redis = mock_redis

    session = BrowserSession(session_id="s1", tenant_id="t1")

    # _register_in_redis should not raise even when Redis fails
    await manager._register_in_redis(session)  # line 223-224: exception silenced


@pytest.mark.asyncio
async def test_browser_session_manager_deregister_redis_exception() -> None:
    """Lines 232-233: Redis delete raises → exception silenced."""
    from app.rpa.session_manager import BrowserSessionManager

    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(side_effect=RuntimeError("Redis down"))

    manager = BrowserSessionManager()
    manager._redis = mock_redis

    # Should not raise
    await manager._deregister_from_redis("s1", "t1")  # line 232-233


@pytest.mark.asyncio
async def test_browser_session_manager_list_active_from_redis_no_redis() -> None:
    """Line 237-238: list_active_from_redis with no Redis → uses list_active()."""
    from app.rpa.session_manager import BrowserSession, BrowserSessionManager

    manager = BrowserSessionManager()
    manager._redis = None

    # Add in-memory session
    session = BrowserSession(session_id="s1", tenant_id="t1")
    manager._sessions[("s1", "t1")] = session

    result = await manager.list_active_from_redis("t1")
    # Returns from list_active (in-memory) since no Redis
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_browser_session_manager_list_active_from_redis_success() -> None:
    """Lines 239-251: list_active_from_redis with Redis → scans keys and parses."""
    import json
    from app.rpa.session_manager import BrowserSession, BrowserSessionManager

    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(return_value=["rpa_session:t1:s1"])
    mock_redis.get = AsyncMock(return_value=json.dumps({
        "session_id": "s1", "tenant_id": "t1", "created_at": "2024-01-01T00:00:00"
    }))

    manager = BrowserSessionManager()
    manager._redis = mock_redis

    result = await manager.list_active_from_redis("t1")
    assert len(result) == 1
    assert result[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_browser_session_manager_list_active_from_redis_exception() -> None:
    """Lines 252-253: Redis.keys raises → falls back to list_active."""
    from app.rpa.session_manager import BrowserSessionManager

    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(side_effect=RuntimeError("Redis unavailable"))

    manager = BrowserSessionManager()
    manager._redis = mock_redis

    result = await manager.list_active_from_redis("t1")
    assert isinstance(result, list)  # fallback to in-memory


def test_browser_session_manager_get_page_finds_alive_session() -> None:
    """Lines 258-260: get_page returns page for alive session."""
    from app.rpa.session_manager import BrowserSession, BrowserSessionManager

    manager = BrowserSessionManager()
    session = BrowserSession(session_id="s1", tenant_id="t1")
    session._browser = MagicMock()  # makes is_alive=True
    session._page = MagicMock()     # the page to return
    manager._sessions[("s1", "t1")] = session

    page = manager.get_page("s1")
    assert page is not None  # line 260


def test_browser_session_manager_get_page_returns_none_not_found() -> None:
    """Line 261: get_page returns None when session not found."""
    from app.rpa.session_manager import BrowserSessionManager

    manager = BrowserSessionManager()
    page = manager.get_page("nonexistent")
    assert page is None  # line 261


# ═══════════════════════════════════════════════════════════════════════════════
# CredentialInjector (credential_injector.py) — vault paths
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_credential_injector_secret_store_exception() -> None:
    """Lines 57-58: secret_store.get_secret raises → warning logged, tries vault."""
    from app.rpa.credential_injector import CredentialInjector

    mock_store = AsyncMock()
    mock_store.get_secret = AsyncMock(side_effect=RuntimeError("store unreachable"))

    injector = CredentialInjector(secret_store=mock_store, tenant_id="t1")
    result = await injector.resolve("vault://my-server/my-key")

    # Falls through to unresolved path — returns original ref
    assert result == "vault://my-server/my-key"


@pytest.mark.asyncio
async def test_credential_injector_vault_resolves_secret() -> None:
    """Lines 63-66: vault.get_secret returns a value → resolved."""
    from app.rpa.credential_injector import CredentialInjector

    mock_vault = AsyncMock()
    mock_vault.get_secret = AsyncMock(return_value="secret-password")

    injector = CredentialInjector(vault=mock_vault, tenant_id="t1")
    result = await injector.resolve("vault://my-secret")

    assert result == "secret-password"


@pytest.mark.asyncio
async def test_credential_injector_vault_exception() -> None:
    """Lines 67-68: vault.get_secret raises → warning logged, returns original ref."""
    from app.rpa.credential_injector import CredentialInjector

    mock_vault = AsyncMock()
    mock_vault.get_secret = AsyncMock(side_effect=RuntimeError("vault down"))

    injector = CredentialInjector(vault=mock_vault, tenant_id="t1")
    result = await injector.resolve("vault://unreachable-secret")

    assert result == "vault://unreachable-secret"


@pytest.mark.asyncio
async def test_credential_injector_resolve_arguments_recursive() -> None:
    """Line 80: resolve_arguments handles nested dicts recursively."""
    from app.rpa.credential_injector import CredentialInjector

    resolved_calls: list[str] = []

    injector = CredentialInjector(tenant_id="t1")

    # Provide mock vault that resolves secrets
    mock_vault = AsyncMock()
    mock_vault.get_secret = AsyncMock(return_value="resolved-value")
    injector._vault = mock_vault

    args = {
        "url": "vault://server/url",
        "nested": {
            "password": "vault://server/pass",
        },
        "plain": "no-vault-here",
    }
    result = await injector.resolve_arguments(args)
    assert result["url"] == "resolved-value"
    assert result["nested"]["password"] == "resolved-value"  # line 80: recursive
    assert result["plain"] == "no-vault-here"


# ═══════════════════════════════════════════════════════════════════════════════
# LocalArtifactStore (artifacts.py) — uncovered paths
# ═══════════════════════════════════════════════════════════════════════════════


def test_local_artifact_store_path_escape_raises() -> None:
    """Line 51: path outside base_dir raises ValueError."""
    from app.rpa.artifacts import RPAArtifactStore

    with tempfile.TemporaryDirectory() as base:
        store = RPAArtifactStore(base_dir=base)
        # Patch Path.is_relative_to to simulate path escape detection
        with patch("pathlib.Path.is_relative_to", return_value=False):
            with pytest.raises(ValueError, match="escapes base directory"):
                store.write_bytes(goal_id="g1", name="file.txt", content=b"data")


@pytest.mark.asyncio
async def test_minio_store_ensure_bucket_create_exception_silenced() -> None:
    """Lines 135-136: create_bucket raises → exception silenced."""
    from app.rpa.artifacts import MinIOArtifactStore

    store = MinIOArtifactStore(
        endpoint_url="http://localhost:9000",
        access_key="key",
        secret_key="secret",
        bucket="test-bucket",
    )

    mock_client = AsyncMock()
    mock_client.head_bucket = AsyncMock(side_effect=Exception("bucket not found"))
    mock_client.create_bucket = AsyncMock(side_effect=Exception("create failed"))

    # _ensure_bucket should not raise even when create_bucket fails
    await store._ensure_bucket(mock_client)  # lines 135-136: silenced


@pytest.mark.asyncio
async def test_minio_store_get_client_requires_aioboto3() -> None:
    """Lines 115-116: _get_client creates aioboto3 session and returns client."""
    from app.rpa.artifacts import MinIOArtifactStore

    store = MinIOArtifactStore(
        endpoint_url="http://localhost:9000",
        access_key="key",
        secret_key="secret",
        bucket="test-bucket",
    )

    mock_client_ctx = MagicMock()
    mock_session = MagicMock()
    mock_session.client = MagicMock(return_value=mock_client_ctx)  # line 116

    mock_aioboto3 = MagicMock()
    mock_aioboto3.Session = MagicMock(return_value=mock_session)

    with patch.dict("sys.modules", {"aioboto3": mock_aioboto3}):
        client = await store._get_client()

    mock_session.client.assert_called_once()  # line 116 covered
    assert client == mock_client_ctx
