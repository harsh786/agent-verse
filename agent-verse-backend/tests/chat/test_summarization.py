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


async def test_rolling_summary_exact_hit_avoids_llm_call() -> None:
    # Same slice summarized twice for one session → exactly one LLM call (cache hit).
    provider = _Provider()
    svc = ChatService(answer_generator=provider)
    old = [{"role": "user", "content": f"m{i}"} for i in range(60)]
    s1 = await svc._summarize_history(old, session_id="sess")
    s2 = await svc._summarize_history(old, session_id="sess")
    assert s1 == s2
    assert len(provider.requests) == 1  # second call served from the rolling cache


async def test_rolling_summary_incremental_only_summarizes_delta() -> None:
    # Growing the slice by a few messages summarizes only the DELTA (a merge call),
    # never re-feeding the whole prefix again.
    provider = _Provider()
    svc = ChatService(answer_generator=provider)
    old = [{"role": "user", "content": f"old-{i}"} for i in range(60)]
    await svc._summarize_history(old, session_id="sess")
    grown = [*old, {"role": "user", "content": "brand-new-delta-msg"}]
    await svc._summarize_history(grown, session_id="sess")
    assert len(provider.requests) == 2  # one initial + one incremental merge
    merge_req = provider.requests[-1]
    joined = " ".join(str(m.content) for m in merge_req.messages)
    assert "brand-new-delta-msg" in joined  # the new message is in the merge input
    assert "old-3" not in joined  # the old prefix is NOT re-sent


async def test_extract_learnings_on_close_writes_durable_memory() -> None:
    written: list[str] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        written.append(fact)

    provider = _Provider()
    svc = ChatService(answer_generator=provider, memory_writer=_writer)
    session = svc.create_session("t1")
    for i in range(6):
        svc.save_message(
            session_id=session.id, tenant_id="t1",
            role="user" if i % 2 == 0 else "assistant", content=f"we decided thing {i}",
        )
    n = await svc.extract_learnings_on_close(session.id, "t1")
    assert n == 1
    assert written and "SUMMARY" in written[0].upper()


async def test_extract_learnings_noop_without_writer_or_history() -> None:
    provider = _Provider()
    svc = ChatService(answer_generator=provider)  # no memory_writer
    session = svc.create_session("t1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="hi")
    assert await svc.extract_learnings_on_close(session.id, "t1") == 0
