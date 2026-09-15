"""Phase 0.3c — post-construction engine wiring for the lifespan swap."""

from __future__ import annotations

from app.chat.service import ChatService


def test_attach_engine_wires_capabilities_partially() -> None:
    svc = ChatService()
    assert svc.can_run_goals is False
    assert svc.can_generate_answers is False

    svc.attach_engine(goal_service=object())
    assert svc.can_run_goals is True
    assert svc.can_generate_answers is False  # not wired yet — partial is fine

    svc.attach_engine(answer_generator=object())
    assert svc.can_run_goals is True  # unchanged
    assert svc.can_generate_answers is True


def test_attach_engine_none_is_noop() -> None:
    svc = ChatService(goal_service=object())
    svc.attach_engine()  # both None
    assert svc.can_run_goals is True  # not cleared
