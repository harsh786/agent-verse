"""Phase 2 — acknowledge-now / deliver-later ("recipe" pattern, R14, scenario 11)."""

from __future__ import annotations

from typing import Any

from app.chat.service import ChatService, extract_delivery_target


def test_extract_delivery_target_carries_channel() -> None:
    ec = {"source": "chat", "session_id": "s1", "message_id": "m1",
          "channel": "whatsapp", "channel_user_id": "+1"}
    assert extract_delivery_target(ec) == {
        "session_id": "s1", "message_id": "m1",
        "channel": "whatsapp", "channel_user_id": "+1",
    }


async def test_acknowledge_now_posts_ack_and_opens_job() -> None:
    svc = ChatService()
    session = svc.create_session("t1")
    job, ack = await svc.acknowledge_async(
        session_id=session.id, tenant_id="t1", origin_message_id="m0"
    )
    assert ack is not None and ack.role == "assistant"
    assert "I'll send it" in ack.content
    assert job.status == "running" and job.session_id == session.id
    # The ack is part of the durable history.
    history = await svc.alist_messages(session.id, "t1")
    assert history[-1].content == ack.content


async def test_deliver_later_posts_result_into_same_thread() -> None:
    svc = ChatService()
    session = svc.create_session("t1")
    job, _ = await svc.acknowledge_async(session_id=session.id, tenant_id="t1")
    msg = await svc.complete_async_job(
        job_id=job.id, tenant_id="t1", content="Here is your espresso comparison."
    )
    assert msg is not None and "espresso" in msg.content
    assert svc.get_async_job(job.id, "t1").status == "done"
    history = await svc.alist_messages(session.id, "t1")
    assert history[-1].content == "Here is your espresso comparison."


async def test_deliver_later_pushes_to_origin_channel() -> None:
    pushed: list[tuple[Any, Any, Any]] = []

    async def _channel_deliver(channel: str, cuid: str | None, text: str) -> None:
        pushed.append((channel, cuid, text))

    svc = ChatService()
    svc.attach_engine(channel_deliver=_channel_deliver)
    session = svc.create_session("t1")
    job, _ = await svc.acknowledge_async(
        session_id=session.id, tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    await svc.complete_async_job(job_id=job.id, tenant_id="t1", content="done!")
    assert pushed == [("whatsapp", "+1", "done!")]


async def test_job_is_tenant_scoped_and_can_fail() -> None:
    svc = ChatService()
    session = svc.create_session("t1")
    job, _ = await svc.acknowledge_async(session_id=session.id, tenant_id="t1")
    # Another tenant cannot see or complete it.
    assert svc.get_async_job(job.id, "t2") is None
    assert await svc.complete_async_job(job_id=job.id, tenant_id="t2", content="x") is None
    # Failure path records the error.
    await svc.fail_async_job(job_id=job.id, tenant_id="t1", error="provider down")
    assert svc.get_async_job(job.id, "t1").status == "error"
