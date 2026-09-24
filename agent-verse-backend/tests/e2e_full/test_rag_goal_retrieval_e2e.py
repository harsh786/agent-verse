"""e2e_full: a goal retrieves ingested knowledge and cites it.

Phase-3 *RAG-patterns* dimension. Documents ingested into a tenant's knowledge
base are retrieved by the agent's hard-wired ``rag_retrieval`` graph node during
goal execution, and the retrieved chunks are surfaced as citations on the
``knowledge_retrieved`` SSE event (source + content + collection_id). Proven
against the booted app with real Postgres + pgvector + Redis, goal run inline.

Determinism: a ``FakeProvider`` embedder (sized to match the fixed-width
``long_term_memory.embedding`` column — see ``_fake_embedder`` below) is pinned
for both ingest and the retrieval gateway (the gateway embeds the query
itself), and the goal text repeats the document's distinctive marker so the
hybrid strategy's lexical leg matches regardless of the (non-semantic) fake
vectors. A fresh tenant is used so the node's "auto-list the tenant's
collections" path targets exactly the one collection ingested here.
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
        messages_text = "\n".join(
            str(getattr(m, "content", "") or "") for m in getattr(request, "messages", [])
        )
        if "steps" in props:
            content = '{"steps": ["Consult the knowledge base and answer"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "answered from the knowledge base"}'
        elif '"relevance"' in messages_text:
            # The default runtime profile routes this factual-QA goal to the
            # "corrective" RAG strategy (app/agent/pattern_assembler.py), whose
            # grade_evidence (app/rag/agentic/patterns/corrective.py) grades
            # retrieved passages via an un-schema'd completion request and
            # expects back exactly {"relevance": [<one float per passage>]}.
            # Score every passage fully relevant so the corrective loop's
            # sufficiency check passes deterministically on the first attempt.
            import re as _re

            passage_count = len(_re.findall(r"^\[\d+\]", messages_text, flags=_re.MULTILINE))
            content = json.dumps({"relevance": [1.0] * max(passage_count, 1)})
        elif "Reformulate the query" in messages_text:
            # corrective.reformulate_query (app/rag/agentic/patterns/corrective.py)
            # is called when persisted evidence is thin (this fixture only ingests
            # one short document, so corrective's "at least 2 relevant chunks"
            # sufficiency bar is never met, and it always reformulates through all
            # MAX_CORRECTIVE_RETRIES attempts before falling back to its
            # best-effort return). It raises "query reformulation produced no
            # change" unless each reply differs from the query it was given — a
            # single fixed canned string breaks on the *second* call, since by
            # then the query already equals the first call's output. Vary the
            # reply by attempt number (parsed from the "Attempt N: <query>"
            # prompt) so it's always distinct from its own input.
            import re as _re2

            attempt_match = _re2.search(r"Attempt (\d+):", messages_text)
            attempt_label = attempt_match.group(1) if attempt_match else "1"
            content = f"Zephyrine axolotl ledger audit (reformulated, attempt {attempt_label})"
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
    """Pin a deterministic embedder for ingest and the retrieval gateway.

    Sized to ``app.memory.long_term._LTM_EMBEDDING_DIM`` (2048) rather than an
    arbitrary literal: RAG's ``knowledge_chunks_*`` tables are per-collection and
    dynamically sized to whatever dimension the ingest embedder actually
    produces (see ``app/rag/store.py::_persist_chunks``), so any of
    ``app.rag.engine._SUPPORTED_EMBEDDING_DIMENSIONS`` works there. But this same
    embedder is also used for ``long_term_memory`` recall/writes
    (``app/memory/long_term.py``), and that table has a single FIXED-width
    ``vector(2048)`` column sized by migration 0122 — there is no per-row
    dimension to adapt to. A mismatched dimension here makes every LTM
    write/recall fail with "expected N dimensions, not <other>" (pgvector
    DataError), which is exactly the bug this fixture used to reproduce when it
    was pinned to a hardcoded 768.

    Deliberately NOT derived from ``settings.embedding_dim``: that setting picks
    which *live* embedder gets wired up and is configured independently (this
    repo's own ``.env`` ships ``EMBEDDING_DIM=1536``), while the LTM column's
    actual width is fixed by whichever migration last resized it (2048, per
    0122) — the two are allowed to drift, and in this checkout they do. Using
    ``settings.embedding_dim`` here would just trade one mismatch (768 vs. 2048)
    for another (1536 vs. 2048). Import the real constant so this fixture always
    matches the schema `alembic upgrade head` actually produces, not whatever a
    given deployment's env vars claim.
    """
    from app.memory.long_term import _LTM_EMBEDDING_DIM

    fake = FakeProvider(embed_dim=_LTM_EMBEDDING_DIM)
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
    """Pin a deterministic LLM for goal execution AND the RAG gateway.

    ``app.state._llm_provider_override`` only reaches the main agent loop
    (planner/executor/verifier, via ``goal_service``) — the retrieval
    gateway resolves its own LLM independently, per tenant, through
    ``RetrievalDependencies.llm_resolver`` (wired in ``app/main.py`` as
    ``_resolve_retrieval_llm``), which for a tenant with no stored LLM config
    falls back to the process-wide default provider — a REAL Anthropic client
    when ``ANTHROPIC_API_KEY`` is set, as it is on this machine. Without also
    overriding the gateway's resolver, RAG strategies that call an LLM
    directly (e.g. "corrective"'s evidence-grading/query-reformulation calls
    in ``app/rag/agentic/patterns/corrective.py``) silently hit the real
    model instead of the pinned fake, making the test's own "Determinism"
    claim false and its outcome depend on real API responses (observed
    failure: the real model's JSON reply didn't happen to include the
    "relevance" key ``grade_evidence`` requires, raising
    ``RetrievalStrategyExecutionError``). Replace the gateway's
    ``llm_resolver`` too so every RAG strategy call is deterministic.

    Also clear ``search_capability``: this fixture's single ingested chunk
    never satisfies "corrective"'s "at least 2 relevant chunks" sufficiency
    bar, so after exhausting its reformulation retries it falls back to a web
    search (``app/rag/agentic/patterns/web_augmented.py``) — which, left
    wired to the real (env-configured) SearXNG capability, tries to reach an
    unreachable host and fails the whole strategy with "backend_outage"
    instead of gracefully returning the persisted evidence it already has.
    With no search capability at all, corrective takes the
    "web_capability_unavailable" path and returns its best-effort persisted
    results, which is what this test actually exercises.
    """
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _CompletingProvider()
    gs._task_queue = None

    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = None
    if gw is not None and hasattr(gw, "dependencies"):

        async def _fake_llm_resolver(_tenant_ctx: Any, _strategy: Any) -> Any:
            from app.rag.gateway import ResolvedLLM

            return ResolvedLLM(
                provider=_CompletingProvider(), model="fake-model", provider_type="fake"
            )

        prev_deps = gw.dependencies
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(
                gw.dependencies,
                llm_resolver=_fake_llm_resolver,
                search_capability=None,
            )
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue
        if gw is not None and prev_deps is not None:
            gw.dependencies = prev_deps


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
