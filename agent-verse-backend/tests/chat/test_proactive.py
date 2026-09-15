"""Phase 9 — proactive outreach consent/rate gate."""

from __future__ import annotations

from app.chat.proactive import ProactivePreferences, evaluate_proactive


def _p(**kw: object) -> ProactivePreferences:
    return ProactivePreferences(**kw)  # type: ignore[arg-type]


def test_allows_when_consented_and_within_limits() -> None:
    d = evaluate_proactive(_p(channels=frozenset({"web"})), now_hour=14, sent_today=0, channel="web")
    assert d.allow and d.reason == "ok"


def test_blocks_when_disabled() -> None:
    d = evaluate_proactive(_p(enabled=False), now_hour=14, sent_today=0, channel="web")
    assert not d.allow and d.reason == "disabled"


def test_blocks_disallowed_channel() -> None:
    d = evaluate_proactive(_p(channels=frozenset({"web"})), now_hour=14, sent_today=0, channel="whatsapp")
    assert not d.allow and d.reason == "channel_not_allowed"


def test_rate_limit() -> None:
    d = evaluate_proactive(_p(max_per_day=3), now_hour=14, sent_today=3, channel="web")
    assert not d.allow and d.reason == "rate_limited"


def test_quiet_hours_same_day_window() -> None:
    prefs = _p(quiet_hours=(9, 17))
    assert not evaluate_proactive(prefs, now_hour=12, sent_today=0, channel="web").allow
    assert evaluate_proactive(prefs, now_hour=18, sent_today=0, channel="web").allow


def test_quiet_hours_wraparound_overnight() -> None:
    prefs = _p(quiet_hours=(22, 7))  # 10pm-7am
    assert not evaluate_proactive(prefs, now_hour=23, sent_today=0, channel="web").allow
    assert not evaluate_proactive(prefs, now_hour=3, sent_today=0, channel="web").allow
    assert evaluate_proactive(prefs, now_hour=9, sent_today=0, channel="web").allow
