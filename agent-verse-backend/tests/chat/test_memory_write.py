"""Phase 1 (write side) — explicit 'remember that ...' facts are persisted."""

from __future__ import annotations

import json
from typing import Any

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
