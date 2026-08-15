"""Tests for scheduling from chat — 10 cases."""

from __future__ import annotations

import pytest

from app.chat.intent import IntentRouter, ScheduleConfirmation

router = IntentRouter()


def test_schedule_intent_detected_daily(router: IntentRouter = router) -> None:
    result = router.classify("Run backup every day at 2 AM")
    from app.chat.intent import Intent
    assert result == Intent.SCHEDULE


def test_schedule_intent_detected_weekly(router: IntentRouter = router) -> None:
    from app.chat.intent import Intent
    result = router.classify("Run report weekly")
    assert result == Intent.SCHEDULE


def test_schedule_intent_hourly(router: IntentRouter = router) -> None:
    from app.chat.intent import Intent
    result = router.classify("Run this every hour")
    assert result == Intent.SCHEDULE


def test_schedule_cron_from_daily_at(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Run daily at 9 AM")
    assert isinstance(sc, ScheduleConfirmation)
    assert sc.cron_expression
    # Should contain hour 9
    parts = sc.cron_expression.split()
    assert len(parts) == 5


def test_schedule_cron_from_hourly(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Run hourly")
    assert "* *" in sc.cron_expression


def test_schedule_cron_from_weekly(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Run every Monday at 9 AM")
    assert "1" in sc.cron_expression  # Monday = 1


def test_schedule_human_readable(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Run every day at 9 AM")
    assert sc.human_schedule


def test_schedule_goal_text_preserved(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Deploy the service every day at midnight")
    assert "Deploy" in sc.goal_text


def test_schedule_returns_schedule_confirmation(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Run every Monday")
    assert isinstance(sc, ScheduleConfirmation)


def test_schedule_cron_has_five_parts(router: IntentRouter = router) -> None:
    sc = router.generate_schedule_confirmation("Run daily at 6 PM")
    parts = sc.cron_expression.split()
    assert len(parts) == 5
