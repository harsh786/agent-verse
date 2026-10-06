"""a02-F040-11 on real Postgres (least-privilege NOBYPASSRLS role).

The unique ``uq_a2a_tasks_hmac_signature`` index makes a second task with the
same signature fail even when it is inserted by another tenant (RLS hides that
row, the index still sees it), so a captured request is accepted once across
every replica without Redis. Unsigned (dev) tasks store NULL and never collide.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_a2a_replay_pg.py -m integration
"""

from __future__ import annotations

import uuid

import pytest

from app.api.a2a import A2AReplayError, _persist_task
from tests.rag._pg import app_engine, seed_tenant, sessions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


def _task(tenant_id: str, signature: str) -> tuple[str, dict[str, str]]:
    task_id = uuid.uuid4().hex
    return task_id, {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "goal": "g",
        "status": "accepted",
        "callback_url": "",
        "requester_agent_id": "",
        "hmac_signature": signature,
    }


async def test_a_signature_is_accepted_once_across_tenants(pg_url: str) -> None:
    ta, tb = uuid.uuid4().hex, uuid.uuid4().hex
    for tid in (ta, tb):
        await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        db = sessions(engine)
        sig = "sha256=" + uuid.uuid4().hex * 2
        await _persist_task(*_task(ta, sig), db)
        with pytest.raises(A2AReplayError):
            await _persist_task(*_task(ta, sig), db)
        with pytest.raises(A2AReplayError):
            await _persist_task(*_task(tb, sig), db)
        # Unsigned dev tasks (no signature) never collide.
        await _persist_task(*_task(ta, ""), db)
        await _persist_task(*_task(ta, ""), db)
    finally:
        await engine.dispose()
