"""Phase 1 — long sessions are LLM-summarized (not a static placeholder)."""

from __future__ import annotations

import json
from typing import Any

from app.chat.service import ChatService


class _Provider:
    """Records every request; answers via complete() (no stream_complete)."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        self.requests.append(request)

        class _R:
            content = "SUMMARY of earlier turns"

        return _R()


async def _collect(gen: Any) -> list[dict]:
    return [json.loads(f[len("data: "):].strip()) async for f in gen]


async def test_long_session_is_summarized_and_compressed() -> None:
    provider = _Provider()
    svc = ChatService(answer_generator=provider)
    session = svc.create_session("t1")
    for i in range(120):
        svc.save_message(
            session_id=session.id,
            tenant_id="t1",
            role="user" if i % 2 == 0 else "assistant",
            content=f"message number {i}",
        )

    await _collect(
        svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m", user_message="message number 118")
    )

    # Two LLM calls: one to summarize, one to answer.
    assert len(provider.requests) >= 2
    summarize_req, answer_req = provider.requests[0], provider.requests[-1]
    # The summarizer was asked to summarize.
    assert any("Summarize this earlier part" in str(m.content) for m in summarize_req.messages)
    # The answer call is COMPRESSED — not all 120 messages, and carries the summary.
    assert len(answer_req.messages) <= svc._ctx.MAX_TURNS + 2  # recent window + summary
    assert any("Earlier conversation summary" in str(m.content) for m in answer_req.messages)


async def test_short_session_is_not_summarized() -> None:
    provider = _Provider()
    svc = ChatService(answer_generator=provider)
    session = svc.create_session("t1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="hi")
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m", user_message="hi"))
    # Only the answer call — no summarization.
    assert len(provider.requests) == 1
