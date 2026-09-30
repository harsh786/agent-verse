"""TRG-20: tenant-supplied chat trigger regexes cannot stall the event loop.

Conversational triggers ran tenant regexes with ``re.search`` on the event loop
with no validation or timeout, so one catastrophic-backtracking pattern froze
the replica for every tenant. Patterns are now validated at create/update time
(length cap, compile check, nested-quantifier rejection) and evaluated with a
time bound.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.triggers.consumers.conversational import (
    conversational_matches,
    validate_conversational_patterns,
)
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec


@pytest.mark.parametrize(
    "pattern", ["(a+)+$", "(a*)*b", "(\\w+\\s?)+$", "(x+x+)+y", "([a-z]+)*$"]
)
def test_catastrophic_keyword_regex_rejected_on_create(pattern: str) -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CHAT_KEYWORD, keyword_pattern=pattern)
    with pytest.raises(ValueError, match="keyword_pattern"):
        validate_spec(spec)


@pytest.mark.parametrize(
    ("field", "ttype"),
    [
        ("email_subject_pattern", TriggerType.EMAIL_INTENT),
        ("email_sender_filter", TriggerType.EMAIL_ARRIVAL),
        ("phone_number_filter", TriggerType.SMS_INBOUND),
    ],
)
def test_catastrophic_filter_regex_rejected(field: str, ttype: TriggerType) -> None:
    spec = TriggerSpec(trigger_type=ttype, **{field: "(a+)+$"})
    with pytest.raises(ValueError, match=field):
        validate_conversational_patterns(spec)


def test_invalid_and_oversized_patterns_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid"):
        validate_conversational_patterns(
            TriggerSpec(trigger_type=TriggerType.SMS_INBOUND, phone_number_filter="(unclosed")
        )
    with pytest.raises(ValueError, match="too long"):
        validate_conversational_patterns(
            TriggerSpec(trigger_type=TriggerType.EMAIL_INTENT, email_subject_pattern="a" * 600)
        )


def test_ordinary_patterns_are_accepted_and_match() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CHAT_KEYWORD, keyword_pattern="^deploy\\s+prod")
    validate_spec(spec)
    assert conversational_matches("chat_keyword", spec, {"text": "deploy  prod now"})
    assert not conversational_matches("chat_keyword", spec, {"text": "rollback"})
    kw = TriggerSpec(trigger_type=TriggerType.CHAT_KEYWORD, keyword_pattern="urgent, outage")
    validate_spec(kw)  # a keyword list is not a regex


def test_matching_a_stored_catastrophic_pattern_is_time_bounded() -> None:
    """A pattern persisted before validation existed still cannot hang the loop."""
    spec = SimpleNamespace(channel_type="", channel_id="", keyword_pattern="(a+)+$")
    start = time.monotonic()
    matched = conversational_matches("chat_keyword", spec, {"text": "a" * 50_000 + "!"})
    assert time.monotonic() - start < 1.0
    assert matched is False
