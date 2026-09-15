"""Phase 0.3d stage 3c — async dispatch persists the turn and classifies intent."""

from __future__ import annotations

import pytest

from app.chat.service import ChatService


async def test_adispatch_saves_user_message_with_intent() -> None:
    svc = ChatService()
    s = await svc.acreate_session("t1")
    res = await svc.adispatch(s.id, "t1", "what is 2+2?")
    assert res["intent"] in {"QA", "GOAL", "CLARIFY", "SCHEDULE"}
    assert res["session_id"] == s.id and res["message_id"]
    msgs = await svc.alist_messages(s.id, "t1")
    assert msgs[-1].role == "user" and msgs[-1].content == "what is 2+2?"
    assert msgs[-1].intent == res["intent"]


async def test_adispatch_missing_session_raises() -> None:
    svc = ChatService()
    with pytest.raises(ValueError, match="not found"):
        await svc.adispatch("nope", "t1", "hi")
