"""PROV-02: chat Q&A is budget-checked, charged, ledgered and uses the tenant's BYOK.

Chat streamed through the raw platform provider: no budget preflight, no charge,
no ledger entry, no tenant BYOK; summaries and decomposition were free too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.chat.service import ChatService
from app.providers import guarded_completion as gc
from app.providers.fake import FakeProvider


async def _collect(gen: Any) -> list[dict[str, Any]]:
    return [json.loads(f[len("data: "):].strip()) async for f in gen]


@dataclass
class _Controller:
    remaining: bool = True
    recorded: list[tuple[str, float]] = field(default_factory=list)

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
        self.recorded.append((tenant_ctx.tenant_id, cost_usd))
        return True

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        return self.remaining


@dataclass
class _Tracker:
    rows: list[tuple[str, str, int]] = field(default_factory=list)

    async def record_llm_usage(self, **kw: Any) -> float:
        self.rows.append((kw["tenant_ctx"].tenant_id, kw["role"], kw["completion_tokens"]))
        return 0.0


@pytest.fixture
def services() -> tuple[_Controller, _Tracker]:
    ctrl, tracker = _Controller(), _Tracker()
    gc.set_platform_cost_services(lambda: (ctrl, tracker))
    return ctrl, tracker


class _Streaming(FakeProvider):
    def __init__(self, text: str) -> None:
        super().__init__(responses=[text])
        self.text = text
        self.streams = 0

    async def stream_complete(self, request: Any):  # type: ignore[no-untyped-def, override]
        self.streams += 1
        for word in self.text.split(" "):
            yield word + " "


def _svc(gen: Any, resolver: Any = None) -> tuple[ChatService, str]:
    svc = ChatService(answer_generator=gen)
    if resolver is not None:
        svc.attach_engine(provider_resolver=resolver)
    session = svc.create_session("t-chat")
    svc.save_message(session_id=session.id, tenant_id="t-chat", role="user", content="2+2?")
    return svc, session.id


async def _ask(svc: ChatService, sid: str) -> list[dict[str, Any]]:
    return await _collect(
        svc.run_qa(session_id=sid, tenant_id="t-chat", message_id="m", user_message="2+2?")
    )


async def test_streamed_answer_is_charged_and_ledgered(services: Any) -> None:
    ctrl, tracker = services
    svc, sid = _svc(_Streaming("Two plus two is four"))
    events = await _ask(svc, sid)
    assert [e["type"] for e in events][-1] == "done"
    assert ctrl.recorded and ctrl.recorded[0][0] == "t-chat" and ctrl.recorded[0][1] > 0
    assert tracker.rows and tracker.rows[0][:2] == ("t-chat", "chat_qa")
    assert tracker.rows[0][2] > 0  # completion tokens of the streamed answer


async def test_exhausted_budget_refuses_before_streaming(services: Any) -> None:
    ctrl, _tracker = services
    ctrl.remaining = False
    gen = _Streaming("never")
    svc, sid = _svc(gen)
    events = await _ask(svc, sid)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[0].get("code") == "llm_budget_exhausted"
    assert gen.streams == 0
    assert not any(m.role == "assistant" for m in svc.list_messages(sid, "t-chat"))


async def test_tenant_byok_provider_answers_instead_of_the_platform(services: Any) -> None:
    platform, byok = _Streaming("platform"), _Streaming("from byok")

    async def _resolver(tenant_id: str) -> Any:
        assert tenant_id == "t-chat"
        return byok

    svc, sid = _svc(platform, _resolver)
    events = await _ask(svc, sid)
    answer = "".join(e["token"] for e in events if e["type"] == "token")
    assert "byok" in answer and platform.streams == 0


async def test_unusable_byok_config_fails_closed_not_to_the_platform(services: Any) -> None:
    from app.providers.tenant_provider import TenantProviderError

    platform = _Streaming("platform")

    async def _resolver(tenant_id: str) -> Any:
        raise TenantProviderError("key cannot be decrypted")

    svc, sid = _svc(platform, _resolver)
    events = await _ask(svc, sid)
    assert any(e["type"] == "error" for e in events)
    assert platform.streams == 0


async def test_non_streaming_provider_goes_through_complete_decision(services: Any) -> None:
    ctrl, _ = services
    svc, sid = _svc(FakeProvider(responses=["four"]))
    events = await _ask(svc, sid)
    assert "four" in "".join(e["token"] for e in events if e["type"] == "token")
    assert ctrl.recorded and ctrl.recorded[0][0] == "t-chat"


async def test_history_summary_is_charged_to_the_tenant(services: Any) -> None:
    ctrl, _ = services
    svc = ChatService(answer_generator=FakeProvider(responses=["a summary"]))
    out = await svc._summarize_history(
        [{"role": "user", "content": "hello"}], session_id="s", tenant_id="t-chat"
    )
    assert out == "a summary"
    assert [t for t, _c in ctrl.recorded] == ["t-chat"]


# ── PROV-29: reasoning tokens are charged too ─────────────────────────────────


class _Chunks(FakeProvider):
    def __init__(self, chunks: list[str], *, stall_after: bool = False) -> None:
        super().__init__(responses=["unused"])
        self.chunks = chunks
        self.stall_after = stall_after

    async def stream_complete(self, request: Any):  # type: ignore[no-untyped-def, override]
        import asyncio

        for c in self.chunks:
            yield c
        if self.stall_after:
            await asyncio.sleep(30)


async def test_reasoning_only_stream_that_stalls_is_charged(
    services: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.chat.service as chat_mod

    monkeypatch.setattr(chat_mod, "_llm_stall_timeout_seconds", lambda: 0.2)
    _ctrl, tracker = services
    reasoning = "<think>" + "x" * 400
    svc, sid = _svc(_Chunks([reasoning], stall_after=True))
    events = await _ask(svc, sid)
    assert any(e["type"] == "error" for e in events)
    assert tracker.rows, "a stream that produced reasoning tokens must be charged"
    assert tracker.rows[0][2] >= 400 // 4


async def test_reasoning_and_answer_charged_on_total(services: Any) -> None:
    _ctrl, tracker = services
    think = "<think>" + "r" * 800 + "</think>"
    answer = "a" * 80
    svc, sid = _svc(_Chunks([think, answer]))
    events = await _ask(svc, sid)
    assert "a" * 80 in "".join(e["token"] for e in events if e["type"] == "token")
    assert tracker.rows[0][2] >= (800 + 80) // 4
