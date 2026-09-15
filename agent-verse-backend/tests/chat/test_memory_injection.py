"""Phase 1 — long-term/episodic memory recalled into QA context.

When a memory-recall hook is wired, a QA turn retrieves relevant memories for the
latest user message and injects them into the LLM context, so the chat "remembers"
across sessions and long delays.
"""

from __future__ import annotations

import json
from typing import Any

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
