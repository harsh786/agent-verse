"""Phase 0.3b — real QA answers (no more echoing "Answering: ...").

A QA-intent chat turn builds context from history via ConversationContext and
streams a real LLM answer, persisting the assistant message. Tested with the
deterministic FakeProvider.
"""

from __future__ import annotations

import json

from app.chat.service import ChatService
from app.providers.fake import FakeProvider


async def _collect(gen) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(f[len("data: "):].strip()) async for f in gen]


async def test_run_qa_streams_real_answer_and_persists() -> None:
    svc = ChatService(answer_generator=FakeProvider(responses=["Two plus two is four"]))
    session = svc.create_session("t1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="what is 2+2?")

    events = await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m1", user_message="what is 2+2?")
    )
    types = [e["type"] for e in events]

    assert types[0] == "message_started"
    assert types[-1] == "done"
    assert "token" in types
    # streamed tokens reconstruct the answer
    answer = "".join(e["token"] for e in events if e["type"] == "token")
    assert "four" in answer
    # assistant message persisted for future context/memory
    msgs = svc.list_messages(session.id, "t1")
    assert any(m.role == "assistant" and "four" in m.content for m in msgs)


async def test_run_qa_includes_history_context() -> None:
    # The provider echoes back how many messages it was given, proving history is fed.
    class _CountingProvider(FakeProvider):
        seen: int = 0

        async def stream_complete(self, request):  # type: ignore[no-untyped-def, override]
            _CountingProvider.seen = len(request.messages)
            yield "ok"

    svc = ChatService(answer_generator=_CountingProvider())
    session = svc.create_session("t1", system_prompt="You are helpful.")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="hi")
    svc.save_message(session_id=session.id, tenant_id="t1", role="assistant", content="hello")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="how are you?")

    await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m2", user_message="how are you?")
    )
    # system prompt + 3 history turns
    assert _CountingProvider.seen == 4


async def test_run_qa_without_generator_is_explicit_error() -> None:
    import pytest

    svc = ChatService()
    session = svc.create_session("t1")
    with pytest.raises(RuntimeError, match="answer generator"):
        await _collect(
            svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m", user_message="hi")
        )


async def test_run_qa_works_with_complete_only_provider() -> None:
    """A provider without stream_complete (only complete()) still works."""
    class _CompleteOnly:
        async def complete(self, request):  # type: ignore[no-untyped-def]
            class _R:
                content = "The answer is 42"
            return _R()

    svc = ChatService(answer_generator=_CompleteOnly())
    session = svc.create_session("t1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="q?")
    events = await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m", user_message="q?")
    )
    assert any(e["type"] == "token" and "42" in e["token"] for e in events)
    msgs = svc.list_messages(session.id, "t1")
    assert any(m.role == "assistant" and "42" in m.content for m in msgs)
