"""CHAT-KB: chat transcripts as knowledge on a real Postgres (owner decision 7).

Postgres runs as a least-privilege NOBYPASSRLS role, so every statement is under
the tenant's RLS. An ``agent_generated`` Source listening for ``chat_transcript``
is synced through the real pipeline into a real collection, then searched:

* user A opted in, user B did not, a session without an owner (an API key's):
  only A's own chat is searchable, cited back to its chat session id;
* the e-mail address and the AWS key A typed are never in the index;
* a new message re-indexes A's session incrementally, an unchanged re-sync
  indexes nothing (dedup);
* another tenant's consented chat is never read nor served;
* A revokes: A's transcript is removed (a held one is kept and reported), the
  next sync indexes nothing of A, and B's state is untouched.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/connectors/test_chat_transcripts_integration.py \\
        -m integration
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ingestion.base_connector import stable_doc_id
from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.services.chat_knowledge import ChatKnowledgeSettings, purge_transcripts
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_DIM = 768
T0 = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
EMAIL = "alice.ops@example.com"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * _DIM for _ in request.texts], model="fake")


async def _session(pg: str, tid: str, owner: str | None, title: str, at: datetime) -> str:
    sid = uuid.uuid4().hex
    await admin_exec(
        pg,
        "INSERT INTO chat_sessions (id, tenant_id, title, owner_user_id, created_at, "
        "updated_at) VALUES (:id, :t, :title, :o, :at, :at)",
        {"id": sid, "t": tid, "title": title, "o": owner, "at": at},
    )
    return sid


async def _message(
    pg: str, tid: str, sid: str, role: str, content: str, at: datetime,
    author: str | None = None,
) -> None:
    await admin_exec(
        pg,
        "INSERT INTO chat_messages (id, session_id, tenant_id, role, content, metadata, "
        "created_at) VALUES (:id, :s, :t, :r, :c, CAST(:m AS jsonb), :at)",
        {"id": uuid.uuid4().hex, "s": sid, "t": tid, "r": role, "c": content,
         "m": json.dumps({"author_user_id": author} if author else {}), "at": at},
    )
    await admin_exec(
        pg, "UPDATE chat_sessions SET updated_at = :at WHERE id = :s", {"s": sid, "at": at}
    )


async def _turn(pg: str, tid: str, sid: str, owner: str | None, q: str, a: str,
                at: datetime) -> None:
    await _message(pg, tid, sid, "user", q, at, author=owner)
    await _message(pg, tid, sid, "assistant", a, at + timedelta(seconds=5))


async def _chunks(pg: str, tid: str) -> list[tuple[str, str, dict[str, Any]]]:
    rows = await admin_exec(
        pg,
        f"SELECT document_id, content, metadata FROM knowledge_chunks_{_DIM} "
        "WHERE tenant_id = :t ORDER BY document_id, chunk_index",
        {"t": tid},
    )
    return [(str(r[0]), str(r[1]), r[2] if isinstance(r[2], dict) else json.loads(r[2]))
            for r in rows]


class _World:
    def __init__(self, pg: str, engine: Any, tid: str) -> None:
        self.pg, self.tid = pg, tid
        self.db = sessions(engine)
        self.ctx = TenantContext(tid, PlanTier.FREE, "k")
        self.store = KnowledgeStore(self.db, embedding_dim=_DIM)
        self.settings = ChatKnowledgeSettings(db=self.db)
        self.connector = AgentGeneratedConnector(db_factory=self.db)
        self.pipeline = IngestionPipeline(knowledge_store=self.store, embedder=_Embedder())
        self.config: SourceConfig | None = None
        self.cursor: str | None = None

    async def source(self) -> None:
        cid = await self.store.create_collection_async(
            KnowledgeCollection(name="chats"), tenant_ctx=self.ctx)
        self.config = SourceConfig(
            source_id=uuid.uuid4().hex, tenant_id=self.tid, name="chats",
            family=SourceFamily.AGENT_GENERATED, source_type="agent_generated",
            collection_id=cid, min_quality_score=0.0,
            connection_config={"source_types": ["chat_transcript"]},
        )

    async def sync(self) -> dict[str, list[str]]:
        assert self.config is not None
        out: dict[str, list[str]] = {"indexed": [], "skipped": []}
        last = self.cursor
        async for doc, nxt in self.connector.get_delta(self.config, self.cursor):
            result = await self.pipeline.ingest(doc, self.config)
            assert result.status in ("indexed", "skipped"), (result.status, result.error)
            out[result.status].append(doc.source_url)
            last = nxt
        self.cursor = self.connector.completed_cursor or last
        return out

    async def search(self, query: str) -> list[dict[str, Any]]:
        assert self.config is not None
        hits = await self.store.hybrid_search_db(
            query, [0.1] * _DIM, self.config.collection_id, self.ctx, top_k=20)
        return [{"content": h.content, "metadata": h.metadata} for h in hits]


async def test_only_consented_owners_chats_are_searchable_and_revoke_removes_them(
    pg_url: str,
) -> None:
    tid, other = uuid.uuid4().hex, uuid.uuid4().hex
    for t in (tid, other):
        await seed_tenant(pg_url, t)
    s_a = await _session(pg_url, tid, "user-a", "Pallet bay rules", T0)
    await _turn(pg_url, tid, s_a, "user-a",
                f"What is the dock door rule for pallet bay seven? Mail {EMAIL}, key {AWS_KEY}",
                "Bay seven dock doors close at 18:00 sharp.", T0)
    # Another person's message written into A's session is never A's transcript.
    await _turn(pg_url, tid, s_a, "user-b", "intruder note about salaries",
                "reply about salaries", T0 + timedelta(minutes=1))
    s_b = await _session(pg_url, tid, "user-b", "Payroll", T0)
    await _turn(pg_url, tid, s_b, "user-b", "Confidential payroll bravo question",
                "Payroll bravo answer", T0)
    s_n = await _session(pg_url, tid, None, "Automation", T0)
    await _turn(pg_url, tid, s_n, None, "unowned charlie question", "charlie answer", T0)
    s_o = await _session(pg_url, other, "user-a", "Other tenant", T0)
    await _turn(pg_url, other, s_o, "user-a", "other tenant delta secret", "delta answer", T0)

    engine = await app_engine(pg_url)
    try:
        world = _World(pg_url, engine, tid)
        await world.source()
        # Nothing before double consent: the switch is off, nobody opted in.
        assert (await world.sync())["indexed"] == []
        await world.settings.set_tenant_enabled(tid, True)
        assert (await world.sync())["indexed"] == []
        await world.settings.set_consent(tid, "user-a", True)
        # The other tenant consents too: its chat is still never read here.
        other_world = _World(pg_url, engine, other)
        await other_world.settings.set_tenant_enabled(other, True)
        await other_world.settings.set_consent(other, "user-a", True)

        assert (await world.sync())["indexed"] == [f"agentverse://chat-sessions/{s_a}"]
        chunks = await _chunks(pg_url, tid)
        assert {c[0] for c in chunks} == {stable_doc_id(world.config, "chat_transcript", s_a)}
        blob = " ".join(c[1] for c in chunks)
        assert "dock door rule for pallet bay seven" in blob and "18:00" in blob
        for absent in (EMAIL, AWS_KEY, "salaries", "payroll", "charlie", "delta"):
            assert absent.lower() not in blob.lower(), absent
        assert chunks[0][2]["origin"] == {
            "kind": "chat_transcript", "chat_session_id": s_a, "user_id": "user-a"}

        hits = await world.search("dock door pallet bay seven")
        assert hits and hits[0]["metadata"]["origin"]["chat_session_id"] == s_a
        assert hits[0]["metadata"]["source_url"] == f"agentverse://chat-sessions/{s_a}"
        for h in await world.search("payroll bravo charlie delta"):
            text = h["content"].lower()
            assert "payroll" not in text and "charlie" not in text and "delta" not in text

        # Incremental: an unchanged re-sync indexes nothing; a new message
        # re-indexes A's session only.
        again = await world.sync()
        assert again["indexed"] == []
        await _turn(pg_url, tid, s_a, "user-a", "And bay eight?", "Bay eight closes at 20:00.",
                    T0 + timedelta(minutes=10))
        assert (await world.sync())["indexed"] == [f"agentverse://chat-sessions/{s_a}"]
        assert "20:00" in " ".join(c[1] for c in await _chunks(pg_url, tid))

        # Tenant isolation: the other tenant's own Source reads only its chat.
        await other_world.source()
        assert (await other_world.sync())["indexed"] == [f"agentverse://chat-sessions/{s_o}"]
        assert "delta" not in " ".join(c[1] for c in await _chunks(pg_url, tid))
        other_hits = await other_world.search("dock door pallet bay seven")
        assert all("dock door" not in h["content"] for h in other_hits)
        live = [d async for d in world.connector.iter_live_doc_ids(world.config)]
        assert live == [stable_doc_id(world.config, "chat_transcript", s_a)]

        # A held second transcript of A survives the revocation and is reported.
        s_a2 = await _session(pg_url, tid, "user-a", "Held chat", T0)
        await _turn(pg_url, tid, s_a2, "user-a", "held echo question about cranes",
                    "cranes answer", T0 + timedelta(minutes=20))
        assert (await world.sync())["indexed"] == [f"agentverse://chat-sessions/{s_a2}"]
        held_doc = stable_doc_id(world.config, "chat_transcript", s_a2)
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
            "VALUES (:t, 'matter', 'document', CAST(:r AS jsonb))",
            {"t": tid, "r": json.dumps([held_doc])},
        )

        # Revoke: A's transcripts go (except the held one), nothing new is read.
        await world.settings.set_consent(tid, "user-a", False)
        report = await purge_transcripts(
            world.store, tid, user_id="user-a", unconsented_only=True,
            settings=world.settings)
        assert (report.removed, report.held, report.held_document_ids) == (1, 1, [held_doc])
        assert {c[0] for c in await _chunks(pg_url, tid)} == {held_doc}
        assert [h for h in await world.search("dock door pallet bay seven")
                if "dock door" in h["content"]] == []
        await _turn(pg_url, tid, s_a, "user-a", "after revoke foxtrot", "foxtrot answer",
                    T0 + timedelta(minutes=30))
        assert (await world.sync())["indexed"] == []
        assert [d async for d in world.connector.iter_live_doc_ids(world.config)] == []
        # The other tenant's consented transcript is untouched.
        assert len(await _chunks(pg_url, other)) >= 1
    finally:
        await engine.dispose()


async def test_the_sweep_after_a_sync_removes_what_a_revocation_raced(pg_url: str) -> None:
    """A page read before a revocation is indexed after the revocation's purge:
    the sync's closing sweep (the scheduler's) removes it, holds excepted."""
    from app.ingestion.scheduler import _sweep_unconsented_transcripts

    tid = uuid.uuid4().hex
    await seed_tenant(pg_url, tid)
    s_a = await _session(pg_url, tid, "user-a", "Race", T0)
    await _turn(pg_url, tid, s_a, "user-a", "golf hotel question", "golf hotel answer", T0)
    engine = await app_engine(pg_url)
    try:
        world = _World(pg_url, engine, tid)
        await world.source()
        await world.settings.set_tenant_enabled(tid, True)
        await world.settings.set_consent(tid, "user-a", True)
        assert world.config is not None
        docs = [d async for d, _ in world.connector.get_delta(world.config, None)]
        assert len(docs) == 1
        # The revocation (and its purge) happens between the read and the write.
        await world.settings.set_consent(tid, "user-a", False)
        assert (await world.pipeline.ingest(docs[0], world.config)).status == "indexed"
        assert len(await _chunks(pg_url, tid)) >= 1
        sweep = await _sweep_unconsented_transcripts(world.config, world.pipeline)
        assert sweep["ran"] is True and sweep["removed"] == 1 and sweep["notices"] == []
        assert await _chunks(pg_url, tid) == []
    finally:
        await engine.dispose()
