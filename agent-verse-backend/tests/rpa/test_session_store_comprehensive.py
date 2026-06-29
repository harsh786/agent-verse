"""Comprehensive tests for app/rpa/session.py and app/rpa/artifacts.py."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rpa.session import RPAManagedSession, RPASession, RPASessionStore
from app.rpa.artifacts import (
    RPAArtifact,
    RPAArtifactStore,
    _RPAArtifactStoreFallback,
    _safe_name,
    get_artifact_store,
)


# ── RPASession ────────────────────────────────────────────────────────────────


def test_rpa_session_defaults() -> None:
    from datetime import UTC

    s = RPASession(session_id="sid", tenant_id="t1", goal_id="g1")
    assert s.status == "created"
    assert s.current_url is None
    assert s.screenshots == []
    assert s.created_at is not None


def test_rpa_session_status_change() -> None:
    s = RPASession(session_id="sid", tenant_id="t1", goal_id="g1")
    s.status = "running"  # type: ignore[assignment]
    assert s.status == "running"


# ── RPAManagedSession ─────────────────────────────────────────────────────────


def test_rpa_managed_session_defaults() -> None:
    s = RPAManagedSession(tenant_id="t1")
    assert s.status == "active"
    assert s.session_id  # auto-generated
    assert s.created_at
    assert s.last_used_at
    assert s.metadata == {}


def test_rpa_managed_session_auto_id() -> None:
    s1 = RPAManagedSession(tenant_id="t1")
    s2 = RPAManagedSession(tenant_id="t1")
    assert s1.session_id != s2.session_id


def test_rpa_managed_session_custom_metadata() -> None:
    s = RPAManagedSession(tenant_id="t1", metadata={"goal_id": "g42"})
    assert s.metadata["goal_id"] == "g42"


# ── RPASessionStore — in-memory fallback (no Redis) ───────────────────────────


async def test_session_store_create() -> None:
    store = RPASessionStore()  # No Redis
    s = await store.create(tenant_id="tenant-a")
    assert s.tenant_id == "tenant-a"
    assert s.status == "active"
    assert s.session_id in store._fallback


async def test_session_store_get_found() -> None:
    store = RPASessionStore()
    s = await store.create(tenant_id="t1")
    fetched = await store.get(s.session_id, tenant_id="t1")
    assert fetched is not None
    assert fetched.session_id == s.session_id


async def test_session_store_get_not_found() -> None:
    store = RPASessionStore()
    result = await store.get("nonexistent-id", tenant_id="t1")
    assert result is None


async def test_session_store_get_wrong_tenant_returns_none() -> None:
    store = RPASessionStore()
    s = await store.create(tenant_id="tenant-a")
    result = await store.get(s.session_id, tenant_id="tenant-b")
    assert result is None


async def test_session_store_list_active_empty() -> None:
    store = RPASessionStore()
    sessions = await store.list_active(tenant_id="unknown-tenant")
    assert sessions == []


async def test_session_store_list_active_returns_active_only() -> None:
    store = RPASessionStore()
    s1 = await store.create(tenant_id="t1")
    s2 = await store.create(tenant_id="t1")
    # Close s2
    await store.close(s2.session_id, tenant_id="t1")

    active = await store.list_active(tenant_id="t1")
    ids = [s.session_id for s in active]
    assert s1.session_id in ids
    assert s2.session_id not in ids


async def test_session_store_list_active_scoped_to_tenant() -> None:
    store = RPASessionStore()
    await store.create(tenant_id="t1")
    await store.create(tenant_id="t2")

    t1_sessions = await store.list_active(tenant_id="t1")
    t2_sessions = await store.list_active(tenant_id="t2")
    assert len(t1_sessions) == 1
    assert len(t2_sessions) == 1


async def test_session_store_close_returns_true() -> None:
    store = RPASessionStore()
    s = await store.create(tenant_id="t1")
    result = await store.close(s.session_id, tenant_id="t1")
    assert result is True


async def test_session_store_close_nonexistent_returns_false() -> None:
    store = RPASessionStore()
    result = await store.close("nonexistent", tenant_id="t1")
    assert result is False


async def test_session_store_close_marks_as_closed() -> None:
    store = RPASessionStore()
    s = await store.create(tenant_id="t1")
    await store.close(s.session_id, tenant_id="t1")
    # After close, the session should be retrievable but with "closed" status
    closed = store._fallback.get(s.session_id)
    assert closed is not None
    assert closed.status == "closed"


# ── RPASessionStore key helpers ───────────────────────────────────────────────


def test_session_store_skey() -> None:
    store = RPASessionStore()
    assert store._skey("abc123") == "rpa_session:abc123"


def test_session_store_tkey() -> None:
    store = RPASessionStore()
    assert store._tkey("t1") == "rpa_tenant_sessions:t1"


# ── RPASessionStore — Redis fallback on error ─────────────────────────────────


async def test_session_store_redis_error_falls_back_to_memory() -> None:
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))
    mock_redis.sadd = AsyncMock(side_effect=ConnectionError("Redis down"))

    store = RPASessionStore(redis=mock_redis)
    # Should fall through to in-memory without raising
    s = await store.create(tenant_id="t1")
    assert s.session_id in store._fallback


async def test_session_store_redis_get_error_falls_back() -> None:
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

    store = RPASessionStore(redis=mock_redis)
    # Seed in-memory fallback directly
    store._fallback["sess-x"] = RPAManagedSession(tenant_id="t1")
    store._fallback["sess-x"].session_id = "sess-x"

    result = await store.get("sess-x", tenant_id="t1")
    assert result is not None


# ── RPAArtifact ───────────────────────────────────────────────────────────────


def test_rpa_artifact_defaults() -> None:
    a = RPAArtifact()
    assert a.artifact_id  # auto-generated UUID
    assert a.uri == ""
    assert a.path == ""
    assert a.name == ""
    assert a.size_bytes == 0


# ── RPAArtifactStore ──────────────────────────────────────────────────────────


def test_rpa_artifact_store_write_creates_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RPAArtifactStore(base_dir=tmpdir)
        content = b"hello world"
        artifact = store.write_bytes(goal_id="goal-1", name="output.txt", content=content)

        assert artifact.size_bytes == len(content)
        assert artifact.name == "output.txt"
        assert Path(artifact.path).exists()
        assert Path(artifact.path).read_bytes() == content


def test_rpa_artifact_store_uri_is_file_uri() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RPAArtifactStore(base_dir=tmpdir)
        artifact = store.write_bytes(goal_id="g1", name="test.png", content=b"data")
        assert artifact.uri.startswith("file://")


def test_rpa_artifact_store_path_traversal_sanitized() -> None:
    """_safe_path_component sanitizes traversal sequences — write succeeds inside base_dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RPAArtifactStore(base_dir=tmpdir)
        # Path traversal attempt: ../../etc → sanitized to just "etc"
        artifact = store.write_bytes(
            goal_id="../../etc",
            name="passwd",
            content=b"test content",
        )
        # Write succeeded because the path was sanitized to be within base_dir
        assert Path(artifact.path).exists()
        assert tmpdir in artifact.path  # Path stays inside base_dir


def test_rpa_artifact_store_safe_goal_id() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RPAArtifactStore(base_dir=tmpdir)
        artifact = store.write_bytes(
            goal_id="goal/with/slashes",
            name="file.txt",
            content=b"data",
        )
        # Should not crash and path should be inside tmpdir
        assert Path(artifact.path).exists()


# ── _RPAArtifactStoreFallback ─────────────────────────────────────────────────


async def test_fallback_store_write_creates_file() -> None:
    store = _RPAArtifactStoreFallback()
    content = b"fallback data"
    artifact = await store.write_bytes(goal_id="goal-fb", name="result.bin", content=content)

    assert artifact.size_bytes == len(content)
    assert Path(artifact.path).exists()
    assert Path(artifact.path).read_bytes() == content


# ── _safe_name ────────────────────────────────────────────────────────────────


def test_safe_name_strips_special_chars() -> None:
    result = _safe_name("my file!@#$.png")
    assert "!" not in result
    assert "@" not in result
    assert "#" not in result
    assert "$" not in result


def test_safe_name_preserves_alphanumeric() -> None:
    result = _safe_name("screenshot123.png")
    assert result == "screenshot123.png"


def test_safe_name_truncates_at_100() -> None:
    long_name = "a" * 200 + ".png"
    result = _safe_name(long_name)
    assert len(result) <= 100


# ── get_artifact_store factory ────────────────────────────────────────────────


def test_get_artifact_store_no_minio_env(monkeypatch) -> None:
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    store = get_artifact_store(use_minio=False)
    assert isinstance(store, _RPAArtifactStoreFallback)


def test_get_artifact_store_force_minio() -> None:
    from app.rpa.artifacts import MinIOArtifactStore

    store = get_artifact_store(use_minio=True)
    assert isinstance(store, MinIOArtifactStore)


def test_get_artifact_store_auto_with_env(monkeypatch) -> None:
    from app.rpa.artifacts import MinIOArtifactStore

    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    store = get_artifact_store()  # use_minio=None → check env
    assert isinstance(store, MinIOArtifactStore)


# ── MinIOArtifactStore._key ────────────────────────────────────────────────────


def test_minio_key_without_prefix() -> None:
    from app.rpa.artifacts import MinIOArtifactStore

    store = MinIOArtifactStore(prefix="")
    key = store._key("artifact-id", "output.png")
    assert key == "artifact-id/output.png"


def test_minio_key_with_prefix() -> None:
    from app.rpa.artifacts import MinIOArtifactStore

    store = MinIOArtifactStore(prefix="rpa/prod")
    key = store._key("a123", "result.txt")
    assert key == "rpa/prod/a123/result.txt"


def test_minio_key_trailing_slash_stripped() -> None:
    from app.rpa.artifacts import MinIOArtifactStore

    store = MinIOArtifactStore(prefix="myprefix/")
    key = store._key("aid", "file")
    assert key == "myprefix/aid/file"
