"""Phase 1 — long-term/episodic memory recalled into QA context.

When a memory-recall hook is wired, a QA turn retrieves relevant memories for the
latest user message and injects them into the LLM context, so the chat "remembers"
across sessions and long delays.
"""

from __future__ import annotations

import json
from typing import Any

from app.chat.memory_adapter import build_memory_recall, build_memory_writer
from app.chat.service import ChatService


class _CapturingProvider:
    """Records the messages it was asked to answer."""

    last_messages: list[Any] = []

    async def complete(self, request: Any) -> Any:
        _CapturingProvider.last_messages = list(request.messages)

        class _R:
            content = "ok"

        return _R()


async def _collect(gen: Any) -> list[dict]:
    return [json.loads(f[len("data: "):].strip()) async for f in gen]


async def test_memory_is_recalled_and_injected_into_qa() -> None:
    calls: list[tuple[str, str]] = []

    async def _recall(query: str, tenant_id: str) -> list[str]:
        calls.append((query, tenant_id))
        return ["The user prefers oat milk", "The user is based in Berlin"]

    provider = _CapturingProvider()
    svc = ChatService(answer_generator=provider, memory_recall=_recall)
    session = svc.create_session("tenant-9")
    svc.save_message(
        session_id=session.id, tenant_id="tenant-9", role="user", content="what's my usual coffee?"
    )

    await _collect(
        svc.run_qa(
            session_id=session.id,
            tenant_id="tenant-9",
            message_id="m1",
            user_message="what's my usual coffee?",
        )
    )

    # recall was called with the latest user message + tenant
    assert calls == [("what's my usual coffee?", "tenant-9")]
    # the recalled memories reached the LLM context
    joined = " ".join(str(m.content) for m in provider.last_messages)
    assert "oat milk" in joined and "Berlin" in joined


async def test_qa_without_memory_hook_still_works() -> None:
    from app.providers.fake import FakeProvider

    svc = ChatService(answer_generator=FakeProvider(responses=["hello"]))  # no memory_recall
    session = svc.create_session("t1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="hi")
    events = await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m", user_message="hi")
    )
    assert any(e["type"] == "token" for e in events)


async def test_recall_with_no_prior_writes_returns_empty_and_qa_still_works() -> None:
    """An empty recall (fresh tenant, no memories yet) must not inject anything or break the turn."""
    from app.providers.fake import FakeProvider

    async def _recall(query: str, tenant_id: str) -> list[str]:
        return []

    svc = ChatService(answer_generator=FakeProvider(responses=["hello"]), memory_recall=_recall)
    session = svc.create_session("fresh-tenant")
    svc.save_message(session_id=session.id, tenant_id="fresh-tenant", role="user", content="hi")
    events = await _collect(
        svc.run_qa(
            session_id=session.id, tenant_id="fresh-tenant", message_id="m", user_message="hi"
        )
    )
    assert any(e["type"] == "token" for e in events)
    assert any(e["type"] == "done" for e in events)


async def test_memory_recall_failure_degrades_gracefully() -> None:
    """A raising memory_recall hook must not fail the QA turn (caught, treated as no memories)."""
    from app.providers.fake import FakeProvider

    async def _recall(query: str, tenant_id: str) -> list[str]:
        raise RuntimeError("vector store unreachable")

    svc = ChatService(answer_generator=FakeProvider(responses=["hello"]), memory_recall=_recall)
    session = svc.create_session("t1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="hi")
    events = await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m", user_message="hi")
    )
    assert any(e["type"] == "token" for e in events)
    assert any(e["type"] == "done" for e in events)


# ── Full round trip: real LongTermMemoryStore + memory_adapter ────────────────


async def test_write_then_recall_round_trip_with_real_store_and_adapters() -> None:
    """A fact remembered in one turn is recalled by a later turn via the real
    LongTermMemoryStore + build_memory_writer/build_memory_recall adapters
    (not test doubles) — the actual integration path wired at startup."""
    from app.memory.long_term import LongTermMemoryStore
    from app.providers.fake import FakeProvider

    store = LongTermMemoryStore()
    svc = ChatService(
        answer_generator=FakeProvider(responses=["ok", "ok"]),
        memory_writer=build_memory_writer(store),
        memory_recall=build_memory_recall(store),
    )
    session = svc.create_session("tenant-rt")

    # Turn 1: remember a fact.
    msg1 = "Remember that my favorite coffee is oat milk flat white."
    svc.save_message(session_id=session.id, tenant_id="tenant-rt", role="user", content=msg1)
    await _collect(
        svc.run_qa(session_id=session.id, tenant_id="tenant-rt", message_id="m1", user_message=msg1)
    )

    # Turn 2: a later, unrelated-looking QA turn should recall it.
    provider = _CapturingProvider()
    svc._answer_generator = provider  # swap in a capturing provider for this turn
    msg2 = "what's my favorite coffee order?"
    svc.save_message(session_id=session.id, tenant_id="tenant-rt", role="user", content=msg2)
    await _collect(
        svc.run_qa(session_id=session.id, tenant_id="tenant-rt", message_id="m2", user_message=msg2)
    )

    joined = " ".join(str(m.content) for m in provider.last_messages)
    assert "oat milk" in joined


async def test_recall_does_not_leak_across_tenant_boundary() -> None:
    """A fact written for one tenant must never surface in another tenant's recall,
    even when both go through the same shared store instance and adapters."""
    from app.memory.long_term import LongTermMemoryStore
    from app.providers.fake import FakeProvider

    store = LongTermMemoryStore()
    writer = build_memory_writer(store)
    recall = build_memory_recall(store)

    svc_a = ChatService(
        answer_generator=FakeProvider(responses=["ok"]), memory_writer=writer, memory_recall=recall
    )
    session_a = svc_a.create_session("tenant-a")
    msg = "Remember that our production database password rotation is on Fridays."
    svc_a.save_message(session_id=session_a.id, tenant_id="tenant-a", role="user", content=msg)
    await _collect(
        svc_a.run_qa(
            session_id=session_a.id, tenant_id="tenant-a", message_id="m1", user_message=msg
        )
    )

    # Tenant B asks a semantically similar question through the same store/adapters.
    provider_b = _CapturingProvider()
    svc_b = ChatService(answer_generator=provider_b, memory_writer=writer, memory_recall=recall)
    session_b = svc_b.create_session("tenant-b")
    query = "when is the database password rotation?"
    svc_b.save_message(session_id=session_b.id, tenant_id="tenant-b", role="user", content=query)
    await _collect(
        svc_b.run_qa(
            session_id=session_b.id, tenant_id="tenant-b", message_id="m2", user_message=query
        )
    )

    joined = " ".join(str(m.content) for m in provider_b.last_messages)
    assert "Fridays" not in joined
    assert "rotation is on" not in joined


# ── app/chat/memory_adapter.py: build_memory_recall (unit-level) ──────────────


async def test_build_memory_recall_skips_entries_with_no_content() -> None:
    class _Blank:
        content = ""

    class _Real:
        content = "a real memory"

    class _Store:
        async def recall_async(self, query: str, ctx: Any, top_k: int = 5) -> list[Any]:
            return [_Blank(), _Real()]

    recall = build_memory_recall(_Store())
    out = await recall("query", "tenant-a")
    assert out == ["a real memory"]


async def test_build_memory_recall_suppresses_store_exceptions_returns_empty_list() -> None:
    class _BoomStore:
        async def recall_async(self, query: str, ctx: Any, top_k: int = 5) -> list[Any]:
            raise RuntimeError("recall backend down")

    recall = build_memory_recall(_BoomStore())
    out = await recall("query", "tenant-a")
    assert out == []
