"""Proactive delivery is durable, tenant-scoped and honest (a10-F227-03/05/06).

* ``ChatService.deliver_proactive`` kept the principal -> thread map in the
  per-process ``_principal_sessions`` dict, so a second replica (or the process
  after a restart) opened a NEW "Proactive" thread for the same principal. It now
  uses the durable ``chat_principal_sessions`` mapping the channel path uses.
* That dict (and the engine's daily counter) was keyed by principal id alone, so an
  explicit principal id shared by two tenants shared one thread map / cap.
* The external channel push ran under ``suppress(Exception)`` — and no channel push
  is wired at all — while ``POST /v1/proactive/signals`` answered
  ``delivered: true``. The push outcome is now reported (``channel_delivered``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.chat.proactive import ProactiveDelivery, ProactivePreferences
from app.chat.service import ChatService
from app.proactive.engine import ProactiveEngine
from app.proactive.planner import ProactiveProposal
from app.proactive.signals import ProactiveSignal, SignalKind
from tests.chat.test_channel_session_durable import _SharedRepo


class _Repo(_SharedRepo):
    async def delete_session(self, session_id: str, tenant_id: str, **_kw: Any) -> bool:
        await asyncio.sleep(0)
        row = self.sessions.get(session_id)
        if row is None or row["tenant_id"] != tenant_id:
            return False
        del self.sessions[session_id]
        return True


def _proactive_threads(repo: _Repo, tenant_id: str) -> set[str]:
    return {
        m["session_id"]
        for m in repo.messages
        if m.get("tenant_id") == tenant_id
        and (m.get("metadata") or {}).get("delivery") == "proactive"
    }


# ── durable principal -> thread mapping ──────────────────────────────────────


async def test_two_replicas_deliver_into_one_thread() -> None:
    repo = _Repo()
    a, b = ChatService(repository=repo), ChatService(repository=repo)

    first = await a.deliver_proactive(principal_id="p1", tenant_id="t1", message="one")
    second = await b.deliver_proactive(principal_id="p1", tenant_id="t1", message="two")

    assert first.session_id == second.session_id
    assert repo.principal_map[("t1", "p1")] == first.session_id
    assert _proactive_threads(repo, "t1") == {first.session_id}


async def test_restart_keeps_the_proactive_thread() -> None:
    repo = _Repo()
    before = await ChatService(repository=repo).deliver_proactive(
        principal_id="p1", tenant_id="t1", message="before"
    )
    after = await ChatService(repository=repo).deliver_proactive(
        principal_id="p1", tenant_id="t1", message="after"
    )
    assert after.session_id == before.session_id


async def test_proactive_reuses_the_principals_channel_thread() -> None:
    # A thread the principal already has (claimed by the channel path) is used.
    repo = _Repo()
    svc = ChatService(repository=repo)
    session = await svc.acreate_session("t1", title="existing")
    repo.principal_map[("t1", "p1")] = session.id

    out = await svc.deliver_proactive(principal_id="p1", tenant_id="t1", message="hi")
    assert out.session_id == session.id


async def test_concurrent_first_deliveries_converge_on_one_thread() -> None:
    repo = _Repo()
    a, b = ChatService(repository=repo), ChatService(repository=repo)
    r1, r2 = await asyncio.gather(
        a.deliver_proactive(principal_id="p1", tenant_id="t1", message="x"),
        b.deliver_proactive(principal_id="p1", tenant_id="t1", message="y"),
    )
    assert r1.session_id == r2.session_id
    # The loser's empty thread is dropped, not left behind.
    assert set(repo.sessions) == {r1.session_id}


# ── tenant scoping ───────────────────────────────────────────────────────────


async def test_same_principal_id_in_two_tenants_gets_two_threads_in_memory() -> None:
    svc = ChatService()
    a = await svc.deliver_proactive(principal_id="shared", tenant_id="tA", message="a")
    b = await svc.deliver_proactive(principal_id="shared", tenant_id="tB", message="b")
    assert a.session_id != b.session_id
    assert svc.list_messages(a.session_id, "tA")[-1].content == "a"
    assert svc.list_messages(b.session_id, "tB")[-1].content == "b"


def _clock() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _signal(tenant: str, principal: str = "shared", channel: str = "web") -> ProactiveSignal:
    return ProactiveSignal(
        kind=SignalKind.MEMORY_FOLLOWUP, tenant_id=tenant, principal_id=principal,
        channel=channel, payload={"note": "water the plants"},
    )


async def test_daily_cap_is_per_tenant_for_a_shared_principal_id() -> None:
    sent: list[str] = []

    async def deliver(signal: ProactiveSignal, _p: ProactiveProposal) -> None:
        sent.append(signal.tenant_id)

    prefs = ProactivePreferences(max_per_day=1)
    eng = ProactiveEngine(
        deliver=deliver, clock=_clock,
        preferences_provider=lambda *_a: prefs,
    )
    assert (await eng.handle(_signal("tA"))).delivered
    # Tenant B's principal with the same id has its own budget.
    assert (await eng.handle(_signal("tB"))).delivered
    assert (await eng.handle(_signal("tA"))).reason == "rate_limited"
    assert sent == ["tA", "tB"]


# ── honest channel reporting ─────────────────────────────────────────────────


async def test_web_delivery_reports_no_channel() -> None:
    out = await ChatService().deliver_proactive(
        principal_id="p1", tenant_id="t1", message="m", channel="web"
    )
    assert isinstance(out, ProactiveDelivery)
    assert out.channel_delivered is None and out.channel_error is None


async def test_channel_delivery_without_a_wired_push_is_reported() -> None:
    out = await ChatService().deliver_proactive(
        principal_id="p1", tenant_id="t1", message="m", channel="telegram",
        channel_user_id="42",
    )
    assert out.channel_delivered is False
    assert out.channel_error == "channel_delivery_not_configured"
    assert out.message is not None  # still in the principal's thread


async def test_failed_channel_push_is_reported_not_swallowed() -> None:
    svc = ChatService()

    async def boom(*_a: Any) -> None:
        raise ConnectionError("telegram down")

    svc.attach_engine(channel_deliver=boom)
    out = await svc.deliver_proactive(
        principal_id="p1", tenant_id="t1", message="m", channel="telegram"
    )
    assert out.channel_delivered is False and out.channel_error == "ConnectionError"


async def test_successful_channel_push_is_reported() -> None:
    svc = ChatService()
    pushed: list[tuple[Any, ...]] = []

    async def push(*a: Any) -> None:
        pushed.append(a)

    svc.attach_engine(channel_deliver=push)
    out = await svc.deliver_proactive(
        principal_id="p1", tenant_id="t1", message="m", channel="telegram",
        channel_user_id="42",
    )
    assert out.channel_delivered is True
    assert pushed == [("telegram", "42", "m")]


def _engine(chat: ChatService, audits: list[dict[str, Any]]) -> ProactiveEngine:
    async def deliver(signal: ProactiveSignal, proposal: ProactiveProposal) -> Any:
        return await chat.deliver_proactive(
            principal_id=signal.principal_id, tenant_id=signal.tenant_id,
            message=proposal.message, channel=signal.channel,
        )

    prefs = ProactivePreferences(channels=frozenset({"web", "telegram"}))
    return ProactiveEngine(
        deliver=deliver, audit=audits.append, clock=_clock,
        preferences_provider=lambda *_a: prefs,
    )


async def test_engine_says_thread_only_when_the_channel_push_did_not_happen() -> None:
    audits: list[dict[str, Any]] = []
    out = await _engine(ChatService(), audits).handle(_signal("t1", "p1", channel="telegram"))
    assert out.delivered is True
    assert out.reason == "delivered_thread_only"
    assert out.channel_delivered is False
    assert audits[-1]["channel_delivered"] is False


async def test_engine_web_delivery_is_plain_delivered() -> None:
    audits: list[dict[str, Any]] = []
    out = await _engine(ChatService(), audits).handle(_signal("t1", "p1"))
    assert out.delivered and out.reason == "delivered" and out.channel_delivered is None


async def test_engine_reports_delivery_failure_and_spends_no_budget() -> None:
    calls = 0

    async def deliver(*_a: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db down")

    audits: list[dict[str, Any]] = []
    prefs = ProactivePreferences(max_per_day=1)
    eng = ProactiveEngine(
        deliver=deliver, audit=audits.append, clock=_clock,
        preferences_provider=lambda *_a: prefs,
    )
    failed = await eng.handle(_signal("t1", "p1"))
    assert failed.delivered is False and failed.reason == "delivery_failed"
    assert audits == []
    # The failed attempt did not use up the principal's single daily slot.
    assert (await eng.handle(_signal("t1", "p1"))).delivered is True
