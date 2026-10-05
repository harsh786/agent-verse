"""P1e-2: a data subject's erasure reaches the knowledge their goals produced.

An agent_generated Source indexes a goal's answer, its approval decisions and
the lessons learned from it (chunk ``metadata.origin.goal_id``). Erasing the
subject deleted the goal but kept that knowledge searchable, and erased tagged
chunks only from ``knowledge_chunks_768`` (the live embedder writes 2048). Held
collections / documents are never erased.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/lifecycle/test_deletion_agent_knowledge_integration.py -m integration
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.lifecycle.deletion_orchestrator import DeletionOrchestrator
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration


def _vec(dim: int) -> str:
    return "[" + ",".join(["0.01"] * dim) + "]"


async def _chunk(
    pg: str, tid: str, cid: str, doc: str, content: str, metadata: dict[str, object],
    dim: int = 2048,
) -> None:
    await admin_exec(
        pg,
        f"INSERT INTO knowledge_chunks_{dim} (tenant_id, collection_id, document_id, "
        "chunk_index, content, content_hash, embedding, metadata) VALUES (:t, :c, :d, 0, "
        ":x, :h, CAST(:e AS vector), CAST(:m AS jsonb))",
        {"t": tid, "c": cid, "d": doc, "x": content, "h": uuid.uuid4().hex, "e": _vec(dim),
         "m": json.dumps(metadata)},
    )


async def _remaining(pg: str, tid: str) -> set[str]:
    rows = await admin_exec(
        pg, "SELECT content FROM knowledge_chunks_2048 WHERE tenant_id = :t "
        "UNION ALL SELECT content FROM knowledge_chunks_768 WHERE tenant_id = :t", {"t": tid},
    )
    return {str(r[0]) for r in rows}


async def _seed(pg: str, tid: str, subject: str) -> tuple[str, str, str]:
    await seed_tenant(pg, tid)
    gid, other_gid = uuid.uuid4().hex, uuid.uuid4().hex
    await admin_exec(
        pg,
        "INSERT INTO goals (id, tenant_id, goal_text, status, execution_context) VALUES "
        "(:g, :t, 'claim for the subject', 'complete', CAST(:ctx AS json)), "
        "(:o, :t, 'unrelated goal', 'complete', CAST('{}' AS json))",
        {"g": gid, "o": other_gid, "t": tid,
         "ctx": json.dumps({"data_principal_id": subject})},
    )
    open_cid, held_cid = uuid.uuid4().hex, uuid.uuid4().hex
    for cid in (open_cid, held_cid):
        await admin_exec(
            pg, "INSERT INTO knowledge_collections (id, tenant_id, name) VALUES (:c, :t, :c)",
            {"c": cid, "t": tid},
        )
    origin = {"origin": {"kind": "goal_output", "goal_id": gid}}
    await _chunk(pg, tid, open_cid, "doc-answer", "answer of the subject goal", origin)
    await _chunk(pg, tid, open_cid, "doc-decision", "approval on the subject goal",
                 {"origin": {"kind": "hitl_decision", "goal_id": gid, "approval_id": "a1"}})
    await _chunk(pg, tid, held_cid, "doc-held", "held answer of the subject goal", origin)
    await _chunk(pg, tid, open_cid, "doc-other", "answer of another goal",
                 {"origin": {"kind": "goal_output", "goal_id": other_gid}})
    await _chunk(pg, tid, open_cid, "doc-tagged", "tagged 2048 chunk", {"subject_ref": subject})
    await _chunk(pg, tid, open_cid, "doc-tagged-768", "tagged 768 chunk",
                 {"subject_ref": subject}, dim=768)
    await admin_exec(
        pg,
        "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
        "VALUES (:t, 'matter', 'collection', CAST(:r AS jsonb))",
        {"t": tid, "r": json.dumps([held_cid])},
    )
    return gid, open_cid, held_cid


async def test_erasure_removes_goal_derived_knowledge_but_not_held(pg_url: str) -> None:
    tid = uuid.uuid4().hex
    other_tid = uuid.uuid4().hex
    subject = f"subject-{uuid.uuid4().hex[:8]}"
    await _seed(pg_url, tid, subject)
    await _seed(pg_url, other_tid, subject)  # same subject ref, another tenant
    engine = await app_engine(pg_url)
    try:
        orchestrator = DeletionOrchestrator(db_factory=sessions(engine))
        dry = await orchestrator.execute_deletion(tid, subject, dry_run=True)
        assert dry.per_store["knowledge_chunks_goal_derived"] == 2
        assert len(await _remaining(pg_url, tid)) == 6

        receipt = await orchestrator.execute_deletion(tid, subject)
        assert receipt.suspended is False
        assert receipt.per_store["knowledge_chunks_goal_derived"] == 2
        assert receipt.per_store["knowledge_chunks"] == 2  # 2048 and 768 tagged chunks
        assert "legal hold" in receipt.notes["knowledge_chunks_held"]
        assert await _remaining(pg_url, tid) == {
            "held answer of the subject goal", "answer of another goal"}
        assert receipt.verified is True
        # The other tenant is untouched.
        assert len(await _remaining(pg_url, other_tid)) == 6
    finally:
        await engine.dispose()
