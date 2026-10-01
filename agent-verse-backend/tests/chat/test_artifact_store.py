"""Phase 4 / ORG-42 -- chat binary artifact store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.chat.artifact_store import ChatArtifactStore, ChatArtifactTooLargeError


class _FakeRepo:
    """Stands in for PostgresChatRepository's artifact methods."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.fail = False

    async def put_artifact(self, **kw: Any) -> None:
        if self.fail:
            raise ConnectionError("db down")
        self.rows[kw["artifact_id"]] = {
            "id": kw["artifact_id"],
            "tenant_id": kw["tenant_id"],
            "kind": kw["kind"],
            "title": kw["title"],
            "mime": kw["mime"],
            "content": kw["content"],
            "expires_at": kw["expires_at"],
        }

    async def get_artifact(
        self, artifact_id: str, tenant_id: str, *, kind: str | None = None
    ) -> dict[str, Any] | None:
        if self.fail:
            raise ConnectionError("db down")
        row = self.rows.get(artifact_id)
        if row is None or row["tenant_id"] != tenant_id or (kind and row["kind"] != kind):
            return None
        return row


async def test_put_and_get_round_trip() -> None:
    store = ChatArtifactStore()
    aid = await store.put(
        tenant_id="t1", content=b"%PDF-1.4...", mime="application/pdf", filename="r.pdf"
    )
    art = await store.get(aid, "t1")
    assert art is not None and art.content == b"%PDF-1.4..." and art.filename == "r.pdf"
    assert art.mime == "application/pdf"


async def test_get_is_tenant_scoped() -> None:
    store = ChatArtifactStore()
    aid = await store.put(tenant_id="t1", content=b"x", mime="text/plain", filename="a.txt")
    assert await store.get(aid, "t2") is None
    assert await store.get("missing", "t1") is None


async def test_oversized_artifact_is_refused() -> None:
    store = ChatArtifactStore(max_bytes=4)
    with pytest.raises(ChatArtifactTooLargeError):
        await store.put(tenant_id="t1", content=b"12345", mime="text/plain", filename="a")


async def test_in_memory_store_is_bounded() -> None:
    store = ChatArtifactStore(memory_entries=3)
    ids = [
        await store.put(tenant_id="t1", content=b"x", mime="text/plain", filename=f"{i}")
        for i in range(5)
    ]
    assert await store.get(ids[0], "t1") is None
    assert await store.get(ids[1], "t1") is None
    assert await store.get(ids[4], "t1") is not None
    assert len(store._items) == 3


async def test_expired_artifact_is_gone() -> None:
    store = ChatArtifactStore(retention_days=0)
    aid = await store.put(tenant_id="t1", content=b"x", mime="text/plain", filename="a")
    assert await store.get(aid, "t1") is None


async def test_attached_repository_holds_the_bytes_not_process_memory() -> None:
    repo = _FakeRepo()
    store = ChatArtifactStore()
    store.attach_repository(repo)
    aid = await store.put(tenant_id="t1", content=b"doc", mime="text/plain", filename="d.txt")
    assert store._items == {}
    assert repo.rows[aid]["kind"] == "document"
    assert repo.rows[aid]["expires_at"] > datetime.now(UTC) + timedelta(days=29)

    # A second store (another replica) on the same repository serves it.
    other = ChatArtifactStore()
    other.attach_repository(repo)
    art = await other.get(aid, "t1")
    assert art is not None and art.content == b"doc" and art.filename == "d.txt"
    assert await other.get(aid, "t2") is None


async def test_repository_failure_raises_never_falls_back_to_memory() -> None:
    repo = _FakeRepo()
    repo.fail = True
    store = ChatArtifactStore()
    store.attach_repository(repo)
    with pytest.raises(ConnectionError):
        await store.put(tenant_id="t1", content=b"doc", mime="text/plain", filename="d")
    assert store._items == {}
    with pytest.raises(ConnectionError):
        await store.get("any", "t1")
