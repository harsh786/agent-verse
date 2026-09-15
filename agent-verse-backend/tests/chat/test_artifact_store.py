"""Phase 4 — chat binary artifact store."""

from __future__ import annotations

from app.chat.artifact_store import ChatArtifactStore


def test_put_and_get_round_trip() -> None:
    store = ChatArtifactStore()
    aid = store.put(tenant_id="t1", content=b"%PDF-1.4...", mime="application/pdf", filename="r.pdf")
    art = store.get(aid, "t1")
    assert art is not None and art.content == b"%PDF-1.4..." and art.filename == "r.pdf"
    assert art.mime == "application/pdf"


def test_get_is_tenant_scoped() -> None:
    store = ChatArtifactStore()
    aid = store.put(tenant_id="t1", content=b"x", mime="text/plain", filename="a.txt")
    assert store.get(aid, "t2") is None
    assert store.get("missing", "t1") is None
