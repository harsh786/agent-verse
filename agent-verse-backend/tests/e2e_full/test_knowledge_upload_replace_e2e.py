"""e2e_full: re-upload replaces, dedup points at the stored document (P1a-4/5).

On the booted app (real Postgres + Redis, least-privilege role): uploading an
edited file under the same name keeps ONE document with the new text, the
chunks the edit did not touch keep their ids, and identical bytes under another
name are deduplicated with the stored document's id.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e_full.test_knowledge_documents_api_e2e import (  # noqa: F401 - fixtures
    _collection,
    fake_embedder,
    kb_client,
)

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _agreement(days: int) -> str:
    filler = " ".join(f"Clause {i}: the vendor keeps records of shipment {i} for audit."
                      for i in range(160))
    return (f"# Vendor agreement\n\n{filler}\n\n## Termination\n\n"
            f"Either party may terminate with a written notice period of {days} days.\n")


async def _upload(c: Any, cid: str, name: str, body: str) -> dict[str, Any]:
    resp = await c.post("/knowledge/ingest/file", data={"collection_id": cid},
                        files={"file": (name, body.encode(), "text/markdown")})
    assert resp.status_code in (200, 201), resp.text
    return dict(resp.json())


async def _chunk_ids(c: Any, cid: str, doc_id: str) -> dict[str, str]:
    resp = await c.get("/knowledge/search", params={
        "q": "vendor records shipment audit termination notice", "collection_id": cid,
        "top_k": 20, "filters": f'{{"document_id": "{doc_id}"}}'})
    assert resp.status_code == 200, resp.text
    return {h["content"]: h["chunk_id"] for h in resp.json()}


async def test_reupload_replaces_and_keeps_unchanged_chunk_ids(
    kb_client: Any, fake_embedder: Any  # noqa: F811 - the imported fixtures
) -> None:
    cid = await _collection(kb_client)
    v1 = await _upload(kb_client, cid, "agreement.md", _agreement(75))
    before = await _chunk_ids(kb_client, cid, v1["document_id"])
    v2 = await _upload(kb_client, cid, "agreement.md", _agreement(120))
    assert v2["document_id"] == v1["document_id"] and v2["replaced"] is True
    assert v2["chunks_created"] == v1["chunks_created"]
    docs = (await kb_client.get(f"/knowledge/collections/{cid}/documents")).json()
    assert [d.get("document_id") or d.get("id") for d in docs["documents"]] == [
        v1["document_id"]]
    after = await _chunk_ids(kb_client, cid, v1["document_id"])
    text = "\n".join(after)
    assert "notice period of 120 days" in text and "notice period of 75 days" not in text
    unchanged = set(before) & set(after)
    assert unchanged and all(before[k] == after[k] for k in unchanged)

    dup = await _upload(kb_client, cid, "agreement-copy.md", _agreement(120))
    assert dup["deduplicated"] is True and dup["document_id"] == v1["document_id"]
