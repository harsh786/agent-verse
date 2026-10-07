"""a10-F242-02 / a10-F242-03: multimodal image honesty and the job store's memory.

* F242-02: with no vision-capable provider the image job was stored as a
  *completed* IMAGE span whose content was the placeholder
  "[Image content - vision provider not configured]".
* F242-03: every job — including its raw ``source_base64`` payload — was cached
  in a per-process dict even with Redis wired, never evicted except by delete(),
  and that copy shadowed newer Redis state written by another replica.
"""

from __future__ import annotations

import pytest

import base64
from typing import Any

from app.multimodal import job_store as js
from app.multimodal.job_store import AssetJobStore
from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality
from app.multimodal.pipeline import MultimodalPipeline
from app.providers.base import CompletionResponse

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()


class _NoVision:
    def supports_vision(self) -> bool:
        return False


class _EmptyVision:
    def supports_vision(self) -> bool:
        return True

    async def complete(self, request: Any) -> CompletionResponse:
        return CompletionResponse(content="   ", model=request.model)


async def test_image_without_vision_provider_fails_with_reason_not_placeholder() -> None:
    pipeline = MultimodalPipeline()
    job = await pipeline.ingest_image(_PNG, "t1", provider=_NoVision())
    assert job.status == "failed"
    assert job.spans == []
    assert job.error and "vision" in job.error.lower()
    assert "embedding_strategy" not in job.metadata


async def test_image_with_no_provider_at_all_fails() -> None:
    job = await MultimodalPipeline().ingest_image(_PNG, "t1")
    assert job.status == "failed"
    assert job.spans == []


@pytest.mark.usefixtures("registry_vision")
async def test_empty_vision_answer_fails_instead_of_storing_an_empty_span() -> None:
    job = await MultimodalPipeline().ingest_image(_PNG, "t1", provider=_EmptyVision())
    assert job.status == "failed"
    assert job.spans == []
    assert "empty" in (job.error or "")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


class _DownRedis(_FakeRedis):
    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        raise ConnectionError("redis down")


def _job(job_id: str = "j1", status: str = "completed") -> AssetIngestionJob:
    return AssetIngestionJob(
        job_id=job_id, tenant_id="t1", asset_type=Modality.IMAGE, status=status,
        source_base64="A" * 50_000,
        spans=[ExtractedSpan(content="a cat", modality=Modality.IMAGE, confidence=0.9)],
    )


async def test_redis_backed_store_keeps_no_process_memory_copy() -> None:
    store = AssetJobStore(redis=_FakeRedis())
    await store.save(_job())
    assert store._memory == {}
    fetched = await store.get("j1", "t1")
    assert fetched is not None and fetched.status == "completed"
    # A read does not re-populate a per-process cache either.
    assert store._memory == {}


async def test_redis_state_from_another_replica_is_not_shadowed() -> None:
    redis = _FakeRedis()
    replica_a, replica_b = AssetJobStore(redis=redis), AssetJobStore(redis=redis)
    await replica_a.save(_job(status="processing"))
    assert (await replica_a.get("j1", "t1")).status == "processing"  # type: ignore[union-attr]
    await replica_b.save(_job(status="completed"))
    assert (await replica_a.get("j1", "t1")).status == "completed"  # type: ignore[union-attr]


async def test_redis_outage_fallback_copy_drops_raw_bytes_and_clears_on_recovery() -> None:
    store = AssetJobStore(redis=_DownRedis())
    await store.save(_job())
    copy = store._memory[("t1", "j1")]
    assert copy.source_base64 is None
    assert await store.get("j1", "t1") is not None
    # Redis comes back: the next save drops the outage copy.
    store._redis = _FakeRedis()
    await store.save(_job())
    assert store._memory == {}


async def test_memory_only_store_never_holds_raw_bytes_and_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(js, "_MEMORY_MAX_JOBS", 3)
    store = AssetJobStore()
    for i in range(5):
        await store.save(_job(job_id=f"j{i}"))
    assert len(store._memory) == 3
    assert await store.get("j0", "t1") is None
    kept = await store.get("j4", "t1")
    assert kept is not None and kept.source_base64 is None
