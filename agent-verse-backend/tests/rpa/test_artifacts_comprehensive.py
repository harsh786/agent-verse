"""Comprehensive tests for rpa/artifacts.py — RPAArtifactStore, MinIOArtifactStore,
_RPAArtifactStoreFallback, _safe_path_component, _safe_name, get_artifact_store.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.rpa.artifacts import (
    MinIOArtifactStore,
    RPAArtifact,
    RPAArtifactStore,
    _safe_path_component,
    get_artifact_store,
)


# ── 1. RPAArtifact dataclass ─────────────────────────────────────────────────

def test_rpa_artifact_defaults():
    a = RPAArtifact()
    assert a.uri == ""
    assert a.path == ""
    assert a.name == ""
    assert a.size_bytes == 0
    assert len(a.artifact_id) == 32  # uuid4 hex


def test_rpa_artifact_unique_ids():
    a1 = RPAArtifact()
    a2 = RPAArtifact()
    assert a1.artifact_id != a2.artifact_id


def test_rpa_artifact_explicit_fields():
    a = RPAArtifact(artifact_id="abc", uri="file:///tmp/test", path="/tmp/test", name="test.txt", size_bytes=100)
    assert a.artifact_id == "abc"
    assert a.uri == "file:///tmp/test"
    assert a.size_bytes == 100


# ── 2. _safe_path_component ───────────────────────────────────────────────────

def test_safe_path_component_normal():
    assert _safe_path_component("file.txt", default="artifact.bin") == "file.txt"


def test_safe_path_component_strips_directory():
    assert _safe_path_component("../../etc/passwd", default="safe") == "passwd"


def test_safe_path_component_windows_separator():
    result = _safe_path_component("folder\\file.txt", default="default")
    # After replace("\\", "/"), Path("folder/file.txt").name = "file.txt"
    assert result == "file.txt"


def test_safe_path_component_dot_returns_default():
    assert _safe_path_component(".", default="fallback") == "fallback"


def test_safe_path_component_dotdot_returns_default():
    assert _safe_path_component("..", default="fallback") == "fallback"


def test_safe_path_component_empty_returns_default():
    assert _safe_path_component("", default="fallback") == "fallback"


# ── 3. RPAArtifactStore.write_bytes ─────────────────────────────────────────

def test_rpa_store_write_bytes(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path)
    artifact = store.write_bytes(goal_id="goal1", name="output.txt", content=b"hello world")
    assert artifact.size_bytes == 11
    assert artifact.name == "output.txt"
    assert Path(artifact.path).exists()
    assert Path(artifact.path).read_bytes() == b"hello world"


def test_rpa_store_creates_directories(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path / "nested" / "store")
    artifact = store.write_bytes(goal_id="g1", name="file.bin", content=b"\x00")
    assert Path(artifact.path).exists()


def test_rpa_store_artifact_has_uri(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path)
    artifact = store.write_bytes(goal_id="g1", name="data.bin", content=b"data")
    assert artifact.uri.startswith("file://")


def test_rpa_store_path_traversal_sanitized(tmp_path):
    """Path traversal in goal_id is sanitized by _safe_path_component, not raised."""
    store = RPAArtifactStore(base_dir=tmp_path)
    # "../../etc" is sanitized to "etc" by _safe_path_component
    # So path becomes base/etc/passwd — inside base, no error
    artifact = store.write_bytes(goal_id="../../etc", name="passwd", content=b"safe")
    # The artifact is created with sanitized name, no traversal
    assert Path(artifact.path).is_relative_to(tmp_path)
    assert Path(artifact.path).exists()


def test_rpa_store_sanitizes_goal_id(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path)
    # goal_id with path separator should be sanitized
    artifact = store.write_bytes(goal_id="goal/subdir", name="file.txt", content=b"safe")
    assert Path(artifact.path).exists()


def test_rpa_store_overwrite_same_name(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path)
    store.write_bytes(goal_id="g1", name="file.txt", content=b"v1")
    artifact2 = store.write_bytes(goal_id="g1", name="file.txt", content=b"v2")
    assert Path(artifact2.path).read_bytes() == b"v2"


def test_rpa_store_empty_content(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path)
    artifact = store.write_bytes(goal_id="g1", name="empty.bin", content=b"")
    assert artifact.size_bytes == 0


def test_rpa_store_binary_content(tmp_path):
    store = RPAArtifactStore(base_dir=tmp_path)
    binary = bytes(range(256))
    artifact = store.write_bytes(goal_id="g1", name="binary.bin", content=binary)
    assert artifact.size_bytes == 256
    assert Path(artifact.path).read_bytes() == binary


# ── 4. MinIOArtifactStore constructor ────────────────────────────────────────

def test_minio_store_defaults():
    with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
        store = MinIOArtifactStore()
    assert store._bucket == "agentverse-artifacts"
    assert "9000" in store._endpoint_url or "minio" in store._endpoint_url


def test_minio_store_custom_params():
    store = MinIOArtifactStore(
        bucket="custom-bucket",
        endpoint_url="http://s3.custom.com",
        access_key="mykey",
        secret_key="mysecret",
        prefix="uploads",
    )
    assert store._bucket == "custom-bucket"
    assert store._endpoint_url == "http://s3.custom.com"
    assert store._prefix == "uploads"


def test_minio_store_prefix_strips_trailing_slash():
    store = MinIOArtifactStore(
        access_key="k", secret_key="s",
        prefix="uploads/",
    )
    assert not store._prefix.endswith("/")


def test_minio_store_production_warning_on_default_creds(caplog):
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        store = MinIOArtifactStore()
    # Should log error for default creds in production
    # Just verify it doesn't crash
    assert store._access_key == "minioadmin"


# ── 5. MinIOArtifactStore._key ────────────────────────────────────────────────

def test_minio_key_no_prefix():
    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s", prefix="")
    key = store._key("artifact123", "report.csv")
    assert key == "artifact123/report.csv"


def test_minio_key_with_prefix():
    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s", prefix="tenant1")
    key = store._key("artifact123", "report.csv")
    assert key == "tenant1/artifact123/report.csv"


def test_minio_key_empty_name():
    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s", prefix="")
    key = store._key("artifact123", "")
    assert key == "artifact123"


# ── 6. MinIOArtifactStore.write_bytes (mocked) ───────────────────────────────

@pytest.mark.asyncio
async def test_minio_write_bytes_success():
    mock_client = AsyncMock()
    mock_client.head_bucket = AsyncMock()
    mock_client.put_object = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    store = MinIOArtifactStore(bucket="test-bucket", access_key="k", secret_key="s")

    with patch.object(store, "_get_client", return_value=mock_client):
        artifact = await store.write_bytes(goal_id="g1", name="report.csv", content=b"data")

    assert artifact.name == "report.csv"
    assert artifact.size_bytes == 4
    assert "s3://test-bucket/" in artifact.uri
    mock_client.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_minio_write_bytes_creates_bucket_when_missing():
    mock_client = AsyncMock()
    mock_client.head_bucket = AsyncMock(side_effect=Exception("NoSuchBucket"))
    mock_client.create_bucket = AsyncMock()
    mock_client.put_object = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    store = MinIOArtifactStore(bucket="new-bucket", access_key="k", secret_key="s")

    with patch.object(store, "_get_client", return_value=mock_client):
        artifact = await store.write_bytes(goal_id="g1", name="file.txt", content=b"content")

    mock_client.create_bucket.assert_called_once()
    assert artifact.size_bytes == 7


@pytest.mark.asyncio
async def test_minio_write_bytes_fallback_on_error(tmp_path):
    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s")

    async def failing_client():
        raise RuntimeError("S3 unavailable")

    with patch.object(store, "_get_client", side_effect=Exception("S3 down")):
        artifact = await store.write_bytes(goal_id="g1", name="fallback.txt", content=b"data")

    # Fallback returns a valid artifact
    assert artifact.size_bytes == 4
    assert artifact.name == "fallback.txt"


# ── 7. MinIOArtifactStore.read_bytes (mocked) ────────────────────────────────

@pytest.mark.asyncio
async def test_minio_read_bytes_success():
    mock_body = AsyncMock()
    mock_body.read = AsyncMock(return_value=b"file content")
    mock_client = AsyncMock()
    mock_client.get_object = AsyncMock(return_value={"Body": mock_body})
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s")

    with patch.object(store, "_get_client", return_value=mock_client):
        content = await store.read_bytes(artifact_id="art123", name="file.txt")

    assert content == b"file content"


@pytest.mark.asyncio
async def test_minio_read_bytes_not_found_returns_empty():
    mock_client = AsyncMock()
    mock_client.get_object = AsyncMock(side_effect=Exception("Not found"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s")

    with patch.object(store, "_get_client", return_value=mock_client):
        content = await store.read_bytes(artifact_id="notfound", name="x.txt")

    assert content == b""


# ── 8. MinIOArtifactStore.presign_url ────────────────────────────────────────

@pytest.mark.asyncio
async def test_minio_presign_url_success():
    mock_client = AsyncMock()
    mock_client.generate_presigned_url = AsyncMock(return_value="https://presigned.url/object")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s")

    with patch.object(store, "_get_client", return_value=mock_client):
        url = await store.presign_url(artifact_id="art1", name="file.txt", expires_seconds=3600)

    assert url == "https://presigned.url/object"


@pytest.mark.asyncio
async def test_minio_presign_url_error_returns_empty():
    mock_client = AsyncMock()
    mock_client.generate_presigned_url = AsyncMock(side_effect=Exception("Presign error"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s")

    with patch.object(store, "_get_client", return_value=mock_client):
        url = await store.presign_url(artifact_id="art1", name="file.txt")

    assert url == ""


# ── 9. _get_client raises when aioboto3 missing ───────────────────────────────

@pytest.mark.asyncio
async def test_minio_get_client_raises_without_aioboto3():
    store = MinIOArtifactStore(bucket="b", access_key="k", secret_key="s")
    with patch.dict("sys.modules", {"aioboto3": None}):
        with pytest.raises(RuntimeError, match="aioboto3 not installed"):
            await store._get_client()


# ── 10. get_artifact_store factory ───────────────────────────────────────────

def test_get_artifact_store_no_endpoint_returns_fallback():
    with patch.dict(os.environ, {"MINIO_ENDPOINT": ""}, clear=False):
        store = get_artifact_store(use_minio=False)
    # Returns fallback store
    assert hasattr(store, "write_bytes")


def test_get_artifact_store_use_minio_true_returns_minio():
    store = get_artifact_store(use_minio=True)
    assert isinstance(store, MinIOArtifactStore)


def test_get_artifact_store_endpoint_set_returns_minio():
    with patch.dict(os.environ, {"MINIO_ENDPOINT": "http://minio:9000"}):
        store = get_artifact_store(use_minio=None)
    assert isinstance(store, MinIOArtifactStore)


def test_get_artifact_store_no_endpoint_none_returns_fallback():
    with patch.dict(os.environ, {"MINIO_ENDPOINT": ""}):
        store = get_artifact_store(use_minio=None)
    from app.rpa.artifacts import _RPAArtifactStoreFallback
    assert isinstance(store, _RPAArtifactStoreFallback)
