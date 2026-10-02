"""Knowledge-base helpers for the complex KB scenarios (HTTP only, like a client)."""

from __future__ import annotations

import time
from typing import Any

from tests.real_world.corpus import CorpusDoc
from tests.real_world.helpers import LiveAPI, body_of, mask, tag


def create_collection(api: LiveAPI, prefix: str, description: str = "") -> str:
    body = api.json_ok("POST", "/knowledge/collections",
                       json={"name": f"{prefix}-{tag()}", "embedder_type": "default",
                             "description": description or "real-world complex corpus"})
    return str(body.get("collection_id") or body.get("id"))


def upload(api: LiveAPI, cid: str, doc: CorpusDoc, *, filename: str | None = None,
           data: bytes | None = None) -> dict[str, Any]:
    """POST /knowledge/ingest/file; returns http status, parsed body and latency."""
    started = time.monotonic()
    resp = api.post("/knowledge/ingest/file", data={"collection_id": cid},
                    files={"file": (filename or doc.filename, data or doc.data, doc.mime)},
                    timeout=600)
    ms = round((time.monotonic() - started) * 1000)
    body = body_of(resp)
    if not isinstance(body, dict):
        body = {"raw": mask(str(body)[:400])}
    return {"http": resp.status_code, "body": body, "ms": ms,
            "chunks": int(body.get("chunks_created") or 0) if resp.status_code < 300 else 0}


def search(api: LiveAPI, cid: str, q: str, top_k: int = 5,
           strategy: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": q, "collection_id": cid, "top_k": top_k}
    if strategy:
        params["strategy"] = strategy
    body = api.json_ok("GET", "/knowledge/search", params=params)
    return list(body if isinstance(body, list) else body.get("results", []))


def rag_query(api: LiveAPI, cid: str, q: str, strategy: str = "hybrid",
              top_k: int = 5) -> tuple[int, dict[str, Any], float]:
    """POST /rag/query → (status, body, latency ms)."""
    started = time.monotonic()
    resp = api.post("/rag/query", json={"query": q, "collection_id": cid,
                                        "strategy": strategy, "top_k": top_k}, timeout=300)
    ms = (time.monotonic() - started) * 1000
    body = body_of(resp)
    return resp.status_code, body if isinstance(body, dict) else {"raw": str(body)[:300]}, ms


def documents_page(api: LiveAPI, cid: str, limit: int = 100, offset: int = 0
                   ) -> dict[str, Any]:
    return dict(api.json_ok("GET", f"/knowledge/collections/{cid}/documents",
                            params={"limit": limit, "offset": offset}))


def all_documents(api: LiveAPI, cid: str, page: int = 100) -> tuple[list[dict[str, Any]], int]:
    """Every document of the collection by paging to the end; (docs, reported total)."""
    docs: list[dict[str, Any]] = []
    offset, total = 0, 0
    while True:
        body = documents_page(api, cid, page, offset)
        batch = list(body.get("documents") or [])
        total = int(body.get("total") or total)
        docs.extend(batch)
        if len(batch) < page:
            return docs, total
        offset += page


def embedding_health(api: LiveAPI, cid: str) -> dict[str, Any]:
    return dict(api.json_ok("GET", f"/embeddings/health/{cid}"))


def doc_title(doc: dict[str, Any]) -> str:
    return str(doc.get("title") or doc.get("source") or "")


def find_document(docs: list[dict[str, Any]], filename: str) -> dict[str, Any] | None:
    return next((d for d in docs if filename.lower() in doc_title(d).lower()), None)


def answer_text(body: dict[str, Any]) -> str:
    ans = body.get("answer")
    if isinstance(ans, dict):
        return str(ans.get("text") or ans.get("answer") or ans)
    return str(ans or "")
