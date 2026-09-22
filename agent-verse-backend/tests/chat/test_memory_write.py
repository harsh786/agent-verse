"""Phase 1 (write side) — explicit 'remember that ...' facts are persisted."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.chat.memory_adapter import build_memory_writer
from app.chat.service import ChatService
from app.providers.fake import FakeProvider


async def _collect(gen: Any) -> list[dict]:
    return [json.loads(f[len("data: "):].strip()) async for f in gen]


async def test_remember_directive_is_written() -> None:
    written: list[tuple[str, str]] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        written.append((fact, tenant_id))

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    session = svc.create_session("t9")
    msg = "Remember that I prefer window seats."
    svc.save_message(session_id=session.id, tenant_id="t9", role="user", content=msg)
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t9", message_id="m", user_message=msg))

    assert written == [("I prefer window seats", "t9")]


async def test_non_directive_writes_nothing() -> None:
    written: list[Any] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        written.append(fact)

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    session = svc.create_session("t9")
    msg = "What's the weather like?"
    svc.save_message(session_id=session.id, tenant_id="t9", role="user", content=msg)
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t9", message_id="m", user_message=msg))

    assert written == []


async def test_memory_writer_failure_does_not_break_the_qa_turn() -> None:
    """Graceful degradation: a raising memory_writer must not fail the chat turn."""

    async def _writer(fact: str, tenant_id: str) -> None:
        raise RuntimeError("store unavailable")

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    session = svc.create_session("t9")
    msg = "Remember that I prefer aisle seats."
    svc.save_message(session_id=session.id, tenant_id="t9", role="user", content=msg)
    events = await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t9", message_id="m", user_message=msg)
    )

    # The turn still completes normally despite the writer blowing up.
    assert any(e["type"] == "token" for e in events)
    assert any(e["type"] == "done" for e in events)


@pytest.mark.parametrize(
    ("message", "expected_fact"),
    [
        ("Remember that I prefer window seats.", "I prefer window seats"),
        ("remember that i prefer window seats", "i prefer window seats"),
        ("Please remember that I prefer window seats.", "I prefer window seats"),
        ("Note that I prefer window seats.", "I prefer window seats"),
        ("Keep in mind I prefer window seats.", "I prefer window seats"),
        ("Don't forget I prefer window seats.", "I prefer window seats"),
        ("Remember I prefer window seats", "I prefer window seats"),
    ],
)
async def test_remember_directive_recognizes_all_supported_phrasings(
    message: str, expected_fact: str
) -> None:
    written: list[tuple[str, str]] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        written.append((fact, tenant_id))

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    session = svc.create_session("t9")
    svc.save_message(session_id=session.id, tenant_id="t9", role="user", content=message)
    await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t9", message_id="m", user_message=message)
    )

    assert written == [(expected_fact, "t9")]


async def test_remember_directive_with_nothing_after_it_writes_nothing() -> None:
    """'Remember .' has no salient fact once stripped — nothing to persist."""
    written: list[Any] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        written.append(fact)

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    session = svc.create_session("t9")
    msg = "Remember ."
    svc.save_message(session_id=session.id, tenant_id="t9", role="user", content=msg)
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t9", message_id="m", user_message=msg))

    assert written == []


async def test_remember_word_mid_sentence_is_not_treated_as_a_directive() -> None:
    """The directive regex must anchor at the start — casual use of 'remember' mid-message
    (not a leading command) must not be misread as a persist-this-fact instruction."""
    written: list[Any] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        written.append(fact)

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    session = svc.create_session("t9")
    msg = "I will always remember that trip fondly."
    svc.save_message(session_id=session.id, tenant_id="t9", role="user", content=msg)
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t9", message_id="m", user_message=msg))

    assert written == []


# ── app/chat/memory_adapter.py: build_memory_writer (unit-level) ──────────────


async def test_build_memory_writer_persists_a_long_term_memory_via_store_async() -> None:
    from app.memory.long_term import LongTermMemoryStore
    from app.tenancy.context import PlanTier, TenantContext

    store = LongTermMemoryStore()
    writer = build_memory_writer(store)
    await writer("I prefer oat milk", "tenant-a")

    ctx = TenantContext(tenant_id="tenant-a", plan=PlanTier.FREE, api_key_id="chat")
    stored = store.list_all(tenant_ctx=ctx)
    assert len(stored) == 1
    assert stored[0].content == "I prefer oat milk"


async def test_build_memory_writer_suppresses_store_exceptions() -> None:
    class _BoomStore:
        async def store_async(self, *, memory: Any, tenant_ctx: Any) -> str:
            raise RuntimeError("db down")

    writer = build_memory_writer(_BoomStore())
    await writer("some fact", "tenant-a")  # must not raise
