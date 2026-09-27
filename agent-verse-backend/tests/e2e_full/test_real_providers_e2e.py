"""e2e_full against REAL model providers — no FakeProvider, no pinned embedder.

Opt-in: ``REAL_PROVIDERS=1`` plus the provider env (e.g. ``ONPREM_*`` for the
self-hosted vLLM cluster and ``NVIDIA_API_KEY``/``NVIDIA_MODEL``). Everything the
other e2e_full tests fake is real here: planner/executor/verifier LLM calls, the
embedder used for ingest and for the retrieval gateway's query embedding, the
hosted reranker, and every RAG strategy's own LLM calls. Backends are the same
real Postgres + Redis (and least-privilege role with ``E2E_LEAST_PRIVILEGE=1``).

Assertions are about behaviour a user would check, not about exact model text:
the right document ranks first, the goal completes, and its answer carries the
fact that exists only in the ingested document.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from .conftest import collect_sse, wait_for_status

if os.getenv("REAL_PROVIDERS") != "1":
    pytest.skip("set REAL_PROVIDERS=1 (and provider env) to run", allow_module_level=True)

pytestmark = [pytest.mark.e2e_full, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

# Facts that exist only in these documents (invented names so no model "knows" them).
_DOCS = {
    "refund": (
        "Kestrelpay refund policy: when a Kestrelpay UPI transfer is declined by the "
        "issuing bank, the customer is refunded within 7 business days. Technical "
        "declines are reversed on the same day."
    ),
    "tokens": (
        "Kestrelpay card tokenization uses the Halcyon vault. Tokens are 19-digit "
        "surrogates and are rotated every 90 days."
    ),
    "security": (
        "Kestrelpay 3-D Secure challenges are only raised when the Tamarind risk "
        "score exceeds 640; below that the flow is frictionless."
    ),
    "unrelated": "The office cafeteria serves lentil soup on Thursdays.",
}


@pytest_asyncio.fixture(loop_scope="session")
async def real_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"real-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Real", "email": email})
    assert resp.status_code == 201, resp.text
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://e2e-full",
        headers={"X-API-Key": resp.json()["api_key"]}, timeout=600,
    ) as c:
        yield c


async def _ingest_corpus(client: Any) -> str:
    coll = await client.post(
        "/knowledge/collections", json={"name": f"real-{uuid.uuid4().hex[:8]}"}
    )
    assert coll.status_code == 201, coll.text
    cid = coll.json()["collection_id"]
    for text in _DOCS.values():
        ing = await client.post(
            "/knowledge/ingest", json={"collection_id": cid, "source_type": "text", "content": text}
        )
        assert ing.status_code == 201, f"ingest failed: {ing.status_code} {ing.text}"
    return cid


async def test_real_embedder_matches_the_configured_dimension(app: Any) -> None:
    from app.core.config import get_settings
    from app.providers.base import EmbedRequest

    embedder = app.state.embedder
    t0 = time.monotonic()
    out = await embedder.embed(EmbedRequest(texts=["hello", "world"]))
    elapsed = time.monotonic() - t0
    dims = {len(v) for v in out.embeddings}
    print(f"\n[real] embedder={type(embedder).__name__} model={out.model} dims={dims} "
          f"{elapsed * 1000:.0f}ms")
    assert dims == {get_settings().embedding_dim}


async def test_real_semantic_search_ranks_the_right_document_first(real_client: Any) -> None:
    cid = await _ingest_corpus(real_client)
    # Paraphrased — shares almost no words with the refund document, so only a
    # real semantic embedder (plus reranker) ranks it first.
    t0 = time.monotonic()
    resp = await real_client.get(
        "/knowledge/search",
        params={"q": "how long until money comes back after the bank rejects a payment?",
                "collection_id": cid, "top_k": 3},
    )
    elapsed = time.monotonic() - t0
    assert resp.status_code == 200, resp.text
    hits = resp.json()
    print(f"\n[real] search {elapsed * 1000:.0f}ms top={[h.get('content', '')[:50] for h in hits]}")
    assert hits, "no results"
    assert "refunded within 7 business days" in hits[0]["content"]


@pytest.fixture
def llm_call_log(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Record every real completion: model, endpoint, seconds, outcome."""
    from app.providers.openai_compatible import OpenAICompatibleProvider

    import traceback

    calls: list[tuple[str, str, float, str]] = []
    original = OpenAICompatibleProvider._complete_once

    def _origin() -> str:
        for frame in reversed(traceback.extract_stack()[:-2]):
            path = frame.filename
            if "/app/" in path and "/providers/" not in path and "traced_provider" not in path:
                return f"{path.split('/app/')[-1]}:{frame.lineno}:{frame.name}"
        return "?"

    async def _timed(self: Any, request: Any) -> Any:
        t0 = time.monotonic()
        model = request.model or self._default_model
        base = _origin()
        try:
            resp = await original(self, request)
        except BaseException as exc:
            calls.append((model, base, time.monotonic() - t0, type(exc).__name__))
            raise
        calls.append((model, base, time.monotonic() - t0, "ok"))
        return resp

    monkeypatch.setattr(OpenAICompatibleProvider, "_complete_once", _timed)
    yield calls
    by_model: dict[str, list[float]] = {}
    for model, base, secs, outcome in calls:
        print(f"[real] llm call {model:28s} {secs:7.1f}s {outcome:22s} {base}")
        by_model.setdefault(model, []).append(secs)
    for model, secs in by_model.items():
        print(f"[real] llm total {model:28s} calls={len(secs)} sum={sum(secs):.1f}s")


async def test_real_goal_answers_from_the_knowledge_base(
    app: Any, real_client: Any, llm_call_log: Any
) -> None:
    await _ingest_corpus(real_client)
    gs = app.state.goal_service
    prev_queue = gs._task_queue
    gs._task_queue = None  # run the goal inline in this process (no Celery worker)
    try:
        t0 = time.monotonic()
        submit = await real_client.post(
            "/goals",
            json={"goal": "According to our knowledge base, within how many business days "
                          "is a customer refunded when a Kestrelpay UPI transfer is declined "
                          "by the issuing bank?"},
        )
        assert submit.status_code == 202, submit.text
        goal_id = submit.json()["goal_id"]
        final = await wait_for_status(
            real_client, goal_id, {"complete", "failed", "cancelled"}, timeout=900, interval=2
        )
        elapsed = time.monotonic() - t0
    finally:
        gs._task_queue = prev_queue

    events = await collect_sse(real_client, goal_id, until="goal_complete", timeout=30)
    types = [(_parse(e) or {}).get("type") for e in events]
    body = json.dumps(final)
    print(f"\n[real] goal status={final.get('status')} in {elapsed:.1f}s events={types}")
    print(f"[real] goal payload: {body[:1500]}")
    for raw in events:
        ev = _parse(raw) or {}
        if ev.get("type") in (
            "model_route_selected", "plan_ready", "step_complete", "claim_grounding_warning",
            "verification_done", "goal_complete", "goal_failed", "error",
        ):
            print(f"[real] event {ev.get('type')}: {json.dumps(ev)[:700]}")
    parsed = [_parse(e) or {} for e in events]
    outputs = " ".join(str(ev.get("output", "")) for ev in parsed if ev.get("type") == "step_complete")
    retrieved = [ev for ev in parsed if ev.get("type") == "knowledge_retrieved"]
    # Guard against a simulated run: the canned FakeProvider answers exactly this.
    assert final.get("provider_warning") is None, final.get("provider_warning")
    assert "Task executed successfully" not in outputs, "goal ran on the simulated provider"
    assert final.get("status") == "complete", f"goal did not complete: {body[:2000]}"
    assert retrieved and retrieved[0].get("chunks_found", 0) > 0, (
        f"the goal retrieved nothing from the ingested collection: {retrieved[:1]}"
    )
    # The fact exists only in the ingested document (the goal text says neither).
    assert "7" in outputs, f"the answer lacks the fact from the document: {outputs[:800]!r}"


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
