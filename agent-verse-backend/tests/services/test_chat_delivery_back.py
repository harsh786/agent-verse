"""Phase 2 — goal completion delivers the result back into the chat conversation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.goal_service import GoalService


class _FakeChat:
    def __init__(self) -> None:
        self.delivered: list[dict[str, Any]] = []

    def deliver_result(self, *, session_id: str, tenant_id: str, content: str, goal_id: str) -> Any:
        self.delivered.append(
            {"session_id": session_id, "tenant_id": tenant_id, "content": content, "goal_id": goal_id}
        )
        return object()


def _call(exec_ctx: dict[str, Any], event: dict[str, Any], chat: Any) -> None:
    gs = SimpleNamespace(_app_state=SimpleNamespace(chat_service=chat))
    record = SimpleNamespace(
        execution_context=exec_ctx, tenant_id="t1", goal_text="do the thing", goal_id="g-1"
    )
    GoalService._deliver_completion_to_chat(gs, record, event)  # type: ignore[arg-type]


def test_delivers_result_to_bound_chat_session() -> None:
    chat = _FakeChat()
    _call(
        {"source": "chat", "session_id": "s1", "message_id": "m1"},
        {"type": "goal_complete", "result": "12 open bugs found"},
        chat,
    )
    assert chat.delivered == [
        {"session_id": "s1", "tenant_id": "t1", "content": "12 open bugs found", "goal_id": "g-1"}
    ]


def test_falls_back_to_completion_message() -> None:
    chat = _FakeChat()
    _call({"source": "chat", "session_id": "s1"}, {"type": "goal_complete"}, chat)
    assert "Completed: do the thing" in chat.delivered[0]["content"]


def test_no_delivery_when_not_chat_sourced() -> None:
    chat = _FakeChat()
    _call({"source": "api"}, {"type": "goal_complete", "result": "x"}, chat)
    assert chat.delivered == []


def test_no_error_without_chat_service() -> None:
    # Must be fail-safe when no chat service is wired.
    gs = SimpleNamespace(_app_state=SimpleNamespace())
    record = SimpleNamespace(
        execution_context={"source": "chat", "session_id": "s1"},
        tenant_id="t1", goal_text="g", goal_id="g-1",
    )
    GoalService._deliver_completion_to_chat(gs, record, {"type": "goal_complete"})  # type: ignore[arg-type]
