"""2.W-5: golden-set regression for NL → TriggerSpec classification.

The deterministic keyword fast-path (``_keyword_route``) is what runs when no LLM
is configured and is the fallback for the LLM path. This golden set pins the
classification for representative phrasings across families so a prompt/regex
change cannot silently regress classification (a mis-classified schedule fires at
the wrong time / off the wrong event).

Cases assert the real, current behaviour (rule order is first-match-wins). A
change here is a deliberate classification change, reviewed as such.
"""

from __future__ import annotations

import pytest

from app.triggers.models import TriggerType
from app.triggers.nl_scheduler import _keyword_route

# (natural-language description, expected TriggerType) — first-match-wins.
_GOLDEN: list[tuple[str, TriggerType]] = [
    # A. Time / schedule
    ("every weekday at 9am", TriggerType.CRON),
    ("every day at midnight", TriggerType.CRON),
    ("run on this cron 0 9 * * 1-5", TriggerType.CRON),
    ("every 30 seconds", TriggerType.INTERVAL),
    ("run once at 2026-01-01T00:00:00Z", TriggerType.ONCE),
    ("deadline is approaching", TriggerType.DEADLINE),
    ("only during business hours", TriggerType.BUSINESS_CALENDAR),
    # B. Goal / agent chain
    ("when the goal finishes", TriggerType.GOAL_COMPLETED),
    ("whenever a task fails", TriggerType.GOAL_FAILED),
    ("if the score is below threshold", TriggerType.GOAL_SCORE_BELOW),
    ("after human approval", TriggerType.HITL_APPROVED),
    ("on human rejection", TriggerType.HITL_REJECTED),
    ("when a new memory is created", TriggerType.MEMORY_CREATED),
    # C. Conversational
    ("when someone types /deploy", TriggerType.CHAT_COMMAND),
    ("on a discord message", TriggerType.DISCORD_EVENT),
    ("when an sms arrives via twilio", TriggerType.SMS_INBOUND),
    # NOTE: rule matches the stem "meeting end"/"meeting finish" (a boundary after
    # "end"); "meeting ends"/"ended" do not match — documented fragility.
    ("trigger on meeting end", TriggerType.MEETING_ENDED),
    ("on form submission", TriggerType.FORM_SUBMISSION),
    # D. Condition / state
    ("on a state machine transition", TriggerType.STATE_TRANSITION),
]


@pytest.mark.parametrize(("description", "expected"), _GOLDEN)
def test_nl_keyword_classification_golden(description: str, expected: TriggerType) -> None:
    spec = _keyword_route(description)
    assert spec is not None, f"{description!r}: no keyword rule matched (regressed)"
    assert spec.trigger_type == expected, (
        f"{description!r} classified {spec.trigger_type.value!r}, expected {expected.value!r}"
    )


def test_interval_extracts_seconds() -> None:
    spec = _keyword_route("every 30 seconds")
    assert spec is not None and spec.trigger_type == TriggerType.INTERVAL
    assert spec.interval_seconds == 30


def test_unmatched_description_returns_none_for_llm_path() -> None:
    # A genuinely novel phrasing must NOT be force-fit by the fast path — it
    # returns None so the LLM (slow) path can handle it.
    assert _keyword_route("xyzzy plugh frobnicate the widget") is None
