"""e2e_full: a goal retrieves ingested knowledge and cites it.

Phase-3 *RAG-patterns* dimension. Documents ingested into a tenant's knowledge
base are retrieved by the agent's hard-wired ``rag_retrieval`` graph node during
goal execution, and the retrieved chunks are surfaced as citations on the
``knowledge_retrieved`` SSE event (source + content + collection_id). Proven
against the booted app with real Postgres + pgvector + Redis, goal run inline.

Determinism: a 768-dim ``FakeProvider`` embedder is pinned for both ingest and
the retrieval gateway (the gateway embeds the query itself), and the goal text
repeats the document's distinctive marker so the hybrid strategy's lexical leg
matches regardless of the (non-semantic) fake vectors. A fresh tenant is used so
the node's "auto-list the tenant's collections" path targets exactly the one
collection ingested here.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import collect_sse, wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_MARKER = "Zephyrine axolotl"
_CONTENT = (
    "The Zephyrine Protocol mandates quarterly axolotl audits across every "
    "regional habitat. Auditors record the distinctive Zephyrine axolotl "
    "coloration in the compliance ledger."
)


class _CompletingProvider(FakeProvider):
    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Consult the knowledge base and answer"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "answered from the knowledge base"}'
        else:
            content = "According to the ledger, the Zephyrine axolotl audit is quarterly."
        return CompletionResponse(content=content, model="fake", input_tokens=6, output_tokens=6)


@pytest_asyncio.fixture(loop_scope="session")
async def rag_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"rag-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Rag", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _fake_embedder(app: Any) -> Any:
    """Pin a deterministic 768-dim embedder for ingest and the retrieval gateway."""
    fake = FakeProvider(embed_dim=768)
    prev_embedder = getattr(app.state, "embedder", None)
    app.state.embedder = fake
    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = None
    if gw is not None and hasattr(gw, "dependencies"):
        prev_deps = gw.dependencies
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(gw.dependencies, embedder=fake)
    try:
        yield
    finally:
        app.state.embedder = prev_embedder
        if gw is not None and prev_deps is not None:
            gw.dependencies = prev_deps


@pytest.fixture
def _inline_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _CompletingProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_goal_retrieves_and_cites_ingested_document(
    rag_client: Any, _fake_embedder: Any, _inline_provider: Any
) -> None:
    # 1. Ingest a distinctive document into the (fresh) tenant's knowledge base.
    coll = await rag_client.post(
        "/knowledge/collections",
        json={"name": f"rag-goal-{uuid.uuid4().hex[:8]}", "description": "rag goal e2e"},
    )
    assert coll.status_code == 201, f"collection create failed: {coll.status_code} {coll.text}"
    collection_id = coll.json()["collection_id"]

    ing = await rag_client.post(
        "/knowledge/ingest",
        json={"collection_id": collection_id, "source_type": "text", "content": _CONTENT},
    )
    assert ing.status_code == 201, f"ingest failed: {ing.status_code} {ing.text}"

    # 2. A goal whose text repeats the marker → the rag_retrieval node searches
    # the tenant's collections and retrieves the chunk.
    submit = await rag_client.post(
        "/goals", json={"goal": f"What does the ledger say about the {_MARKER} audit?"}
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    await wait_for_status(rag_client, goal_id, {"complete", "failed"}, timeout=30.0)

    # 3. The knowledge_retrieved event carries the retrieved chunk as a citation.
    events = await collect_sse(rag_client, goal_id, until="goal_complete", timeout=15.0)
    retrieved = [
        p
        for p in (_parse(raw) for raw in events)
        if p and p.get("type") == "knowledge_retrieved"
    ]
    assert retrieved, (
        "no knowledge_retrieved event — the goal did not retrieve from the "
        f"ingested collection. events: {[_type(r) for r in events]}"
    )
    ev = retrieved[0]
    assert ev.get("chunks_found", 0) > 0, f"retrieval found no chunks: {ev!r}"
    citations = ev.get("citations") or []
    assert citations, f"knowledge_retrieved carried no citations: {ev!r}"
    # The citation cites the ingested collection and carries the source content.
    assert any(c.get("collection_id") == collection_id for c in citations), (
        f"no citation attributed to the ingested collection {collection_id}: {citations!r}"
    )
    assert any("axolotl" in (c.get("content") or "").lower() for c in citations), (
        f"cited chunks did not contain the ingested content: {citations!r}"
    )


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        return None


def _type(raw: str) -> str | None:
    parsed = _parse(raw)
    return parsed.get("type") if parsed else None
