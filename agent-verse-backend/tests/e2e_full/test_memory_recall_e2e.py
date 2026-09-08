"""e2e_full: a goal writes execution memory; a later goal can recall it.

Phase-3 *memory* dimension. On successful completion the wired verifier writes
the goal into the tenant's ``LongTermMemoryStore`` (an auto-extracted
``success_pattern``) and ``ExecutionMemory`` — the same stores the agent loop's
``rag_retrieval`` node recalls from on the *next* goal to seed planning. Proven
against the booted app with real Postgres + Redis, goals run inline.

Recall is injected silently into the planner's context (there is no dedicated
"memory recalled" SSE frame), so the observable seam is the memory API, which
reads the very same wired store instance the loop recalls from:
``GET /memory/long-term`` (durable, in-memory list) proves the write, and
``GET /memory/recall`` proves it is retrievable by the recall mechanism.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_MARKER = "Nebula ferret"


class _CompletingProvider(FakeProvider):
    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Catalog the requested patterns"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "catalog produced"}'
        else:
            content = "Cataloged the requested migration patterns successfully."
        return CompletionResponse(content=content, model="fake", input_tokens=6, output_tokens=6)


@pytest_asyncio.fixture(loop_scope="session")
async def memory_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"memory-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Memory", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


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


async def test_completed_goal_writes_recallable_memory(
    memory_client: Any, _inline_provider: Any
) -> None:
    goal_text = f"Catalog the {_MARKER} migration patterns"
    submit = await memory_client.post("/goals", json={"goal": goal_text})
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]
    final = await wait_for_status(memory_client, goal_id, "complete", timeout=30.0)
    assert str(final.get("status")) == "complete"

    # WRITE: the completion wrote an auto-extracted success_pattern that names
    # this goal — read back from the same wired store the loop recalls from.
    lt = await memory_client.get("/memory/long-term")
    assert lt.status_code == 200, f"{lt.status_code} {lt.text}"
    entries = lt.json()
    matching = [e for e in entries if _MARKER in (e.get("content") or "")]
    assert matching, (
        f"goal completion did not write a recallable memory naming {_MARKER!r}: {entries!r}"
    )
    entry = matching[0]
    assert entry.get("memory_type") == "success_pattern", f"unexpected memory_type: {entry!r}"
    assert "auto-extracted" in (entry.get("tags") or []), f"not auto-extracted: {entry!r}"

    # RECALL: the same recall_async the loop's rag_retrieval node uses surfaces
    # the written memory for a query about this domain.
    rec = await memory_client.get("/memory/recall", params={"q": _MARKER, "limit": 5})
    assert rec.status_code == 200, f"{rec.status_code} {rec.text}"
    results = rec.json()["results"]
    assert any(_MARKER in (r.get("content") or "") for r in results), (
        f"recall did not surface the written memory for {_MARKER!r}: {results!r}"
    )


async def test_later_goal_runs_against_populated_memory(
    memory_client: Any, _inline_provider: Any
) -> None:
    """A first goal populates memory; a second, later goal in the same tenant
    completes with the recall-augmented planning path exercised end-to-end."""
    first = await memory_client.post(
        "/goals", json={"goal": f"Study the {_MARKER} habitat"}
    )
    assert first.status_code == 202
    await wait_for_status(memory_client, first.json()["goal_id"], "complete", timeout=30.0)

    # Second goal: the rag_retrieval node recalls the first goal's memory into
    # the planner context before planning. It must run to completion cleanly.
    second = await memory_client.post(
        "/goals", json={"goal": f"Extend the {_MARKER} study with new findings"}
    )
    assert second.status_code == 202
    final = await wait_for_status(
        memory_client, second.json()["goal_id"], "complete", timeout=30.0
    )
    assert str(final.get("status")) == "complete"
