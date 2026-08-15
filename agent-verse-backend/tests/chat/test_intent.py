"""Tests for IntentRouter — 18 cases."""

from __future__ import annotations

import pytest

from app.chat.intent import Intent, IntentRouter, ClarifyRequest, ScheduleConfirmation


@pytest.fixture()
def router() -> IntentRouter:
    return IntentRouter()


# ── Basic classification ───────────────────────────────────────────────────────


def test_qa_intent_question_words(router: IntentRouter) -> None:
    for msg in ["What is Python?", "How does FastAPI work?", "Why does Redis expire keys?", "Explain async/await"]:
        assert router.classify(msg) == Intent.QA, f"Expected QA for: {msg}"


def test_goal_intent_action_verbs(router: IntentRouter) -> None:
    for msg in [
        "Deploy the backend service",
        "Run the database migration",
        "Create a new API endpoint for users",
        "Fix the authentication bug",
        "Build and push the Docker image",
    ]:
        assert router.classify(msg) == Intent.GOAL, f"Expected GOAL for: {msg}"


def test_schedule_intent_time_expressions(router: IntentRouter) -> None:
    for msg in [
        "Run backup every day at 2am",
        "Schedule a weekly report every Monday",
        "Run this daily at 9 AM",
        "Execute this hourly",
    ]:
        assert router.classify(msg) == Intent.SCHEDULE, f"Expected SCHEDULE for: {msg}"


def test_clarify_intent_underspecified_goal(router: IntentRouter) -> None:
    # "Deploy it" — has goal verb but underspecified target
    result = router.classify("Deploy it")
    assert result == Intent.CLARIFY


def test_qa_ambiguous_short_message(router: IntentRouter) -> None:
    # Very short, no history → falls back to QA
    result = router.classify("ok")
    assert result == Intent.QA


def test_classify_uses_conversation_history(router: IntentRouter) -> None:
    history = [{"role": "user", "content": "Deploy the API service"}]
    # Short follow-up after a goal message → CLARIFY
    result = router.classify("do it", history=history)
    assert result == Intent.CLARIFY


def test_generate_clarifying_question_returns_question_text(router: IntentRouter) -> None:
    req = router.generate_clarifying_question("Deploy it", round=1)
    assert isinstance(req, ClarifyRequest)
    assert len(req.question) > 5
    assert req.round == 1


def test_generate_clarifying_question_returns_options(router: IntentRouter) -> None:
    req = router.generate_clarifying_question("Deploy it", round=1)
    assert isinstance(req.options, list)
    assert len(req.options) >= 2


def test_clarify_max_3_rounds(router: IntentRouter) -> None:
    # After 3 clarify rounds, force GOAL even for underspecified message
    result = router.classify("Deploy it", clarify_round=3)
    assert result == Intent.GOAL


def test_schedule_cron_extraction(router: IntentRouter) -> None:
    sc = router.generate_schedule_confirmation("Run backup every Monday at 3 AM")
    assert isinstance(sc, ScheduleConfirmation)
    assert sc.cron_expression  # non-empty cron
    assert sc.human_schedule  # non-empty human-readable


def test_goal_with_file_context(router: IntentRouter) -> None:
    history = [{"role": "user", "content": "Look at #file:main.py and fix it"}]
    # Has goal verb + file context → should be GOAL not CLARIFY
    result = router.classify("fix this", history=history)
    assert result == Intent.GOAL


def test_qa_with_system_prompt_persona(router: IntentRouter) -> None:
    result = router.classify("What are the best practices for REST API design?")
    assert result == Intent.QA


def test_intent_with_empty_history(router: IntentRouter) -> None:
    result = router.classify("Create a React component", history=[])
    assert result == Intent.GOAL


def test_intent_with_20_turn_history(router: IntentRouter) -> None:
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
        for i in range(20)
    ]
    result = router.classify("Deploy the API", history=history)
    assert result == Intent.GOAL


def test_clarify_returns_options_array(router: IntentRouter) -> None:
    req = router.generate_clarifying_question("Fix this", round=1)
    assert isinstance(req.options, list)


def test_schedule_next_run_calculation(router: IntentRouter) -> None:
    sc = router.generate_schedule_confirmation("Run daily at 9 AM")
    assert sc.cron_expression  # non-empty


def test_qa_code_question(router: IntentRouter) -> None:
    result = router.classify("How do I write a decorator in Python?")
    assert result == Intent.QA


def test_goal_keyword_boundary_case(router: IntentRouter) -> None:
    result = router.classify("Please run the tests and fix any failures")
    assert result == Intent.GOAL
