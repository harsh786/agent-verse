"""Tests for LargePayloadStore."""
from __future__ import annotations

import json

import pytest

from app.workflow.storage import LargePayloadStore


@pytest.fixture
def store() -> LargePayloadStore:
    return LargePayloadStore(threshold_bytes=100)


@pytest.mark.asyncio
async def test_small_payload_passthrough(store: LargePayloadStore) -> None:
    """Payloads under threshold are returned unchanged."""
    small = {"key": "value"}
    result = await store.store_if_large("run1", "step1", small)
    assert result == small


@pytest.mark.asyncio
async def test_large_payload_creates_ref(store: LargePayloadStore) -> None:
    """Payloads over threshold are replaced with a ref pointer."""
    big = {"data": "x" * 200}
    result = await store.store_if_large("run2", "step2", big)
    assert isinstance(result, dict)
    assert "__ref__" in result
    assert result["__ref__"].startswith("s3://")


@pytest.mark.asyncio
async def test_resolve_ref_roundtrip(store: LargePayloadStore) -> None:
    """Stored large payload can be retrieved via resolve_ref."""
    original = {"nested": {"deep": ["a" * 50, "b" * 50, "c" * 50]}, "num": 42}
    ref = await store.store_if_large("run3", "step3", original)
    assert "__ref__" in ref  # was stored
    resolved = await store.resolve_ref(ref)
    assert resolved == original


@pytest.mark.asyncio
async def test_resolve_non_ref_passthrough(store: LargePayloadStore) -> None:
    """Non-ref dicts are returned unchanged by resolve_ref."""
    plain = {"not": "a ref"}
    result = await store.resolve_ref(plain)
    assert result == plain


@pytest.mark.asyncio
async def test_resolve_non_dict_passthrough(store: LargePayloadStore) -> None:
    """Non-dict values pass through resolve_ref."""
    assert await store.resolve_ref("hello") == "hello"
    assert await store.resolve_ref(42) == 42
    assert await store.resolve_ref(None) is None


@pytest.mark.asyncio
async def test_serialize_handles_non_serializable(store: LargePayloadStore) -> None:
    """serialize falls back to str() for non-JSON-serializable objects."""
    import datetime
    obj = {"dt": datetime.datetime(2024, 1, 1)}
    raw = store._serialize(obj)
    assert isinstance(raw, bytes)
    parsed = json.loads(raw)
    assert "dt" in parsed


@pytest.mark.asyncio
async def test_threshold_boundary(store: LargePayloadStore) -> None:
    """Payload exactly at threshold is NOT stored."""
    # threshold is 100 bytes
    payload = {"k": "v"}
    raw = store._serialize(payload)
    assert len(raw) <= 100
    result = await store.store_if_large("run4", "step4", payload)
    assert result == payload  # passthrough


@pytest.mark.asyncio
async def test_in_memory_fallback_no_s3(store: LargePayloadStore) -> None:
    """Without S3 client, falls back to in-memory dict."""
    assert store._s3 is None
    big = {"a": "b" * 200}
    ref = await store.store_if_large("r", "s", big)
    assert "__ref__" in ref
    # In-memory store should contain the data
    key = ref["__ref__"].removeprefix("s3://agentverse-workflow-payloads/")
    assert key in store._mem
