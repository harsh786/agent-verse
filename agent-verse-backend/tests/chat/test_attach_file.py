"""Phase 4 — attach a file to a session as conversation context."""

from __future__ import annotations

from app.chat.service import ChatService


async def test_attach_file_saves_parsed_content_as_message() -> None:
    svc = ChatService()
    session = svc.create_session("t1")
    msg = await svc.attach_file(
        session_id=session.id, tenant_id="t1",
        content_bytes=b"quarterly revenue was 1.2M", filename="q3.txt",
    )
    assert msg is not None and msg.role == "user"
    assert "quarterly revenue was 1.2M" in msg.content
    assert msg.metadata.get("attachment") == "q3.txt"
    # present in history, so the next turn sees it
    assert any("1.2M" in m.content for m in svc.list_messages(session.id, "t1"))


async def test_attach_file_missing_session_returns_none() -> None:
    svc = ChatService()
    out = await svc.attach_file(
        session_id="nope", tenant_id="t1", content_bytes=b"x", filename="a.txt"
    )
    assert out is None
