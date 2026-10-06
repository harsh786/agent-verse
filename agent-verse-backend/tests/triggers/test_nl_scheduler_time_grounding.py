"""B1-10: NL schedule creation knows the current time and speaks IANA timezones.

Live (2026-10-06, POST /nl/schedule on the enterprise tenant):
* "every weekday at 9:30 IST ..." -> cron with timezone "IST" -> 422 (an
  abbreviation, not a zone; before B1-2 it silently ran in UTC);
* "in 20 minutes remind ..." -> relative_delay with no base -> 422;
* "tomorrow at 8am UTC ..." -> once with no fire_at_iso -> 422;
* "every minute ..." on the free plan -> interval with no seconds -> 422
  "interval_seconds > 0" instead of the plan-floor refusal.
The model was never told the current time, so it could not resolve a relative
or one-off time, and nothing mapped a common abbreviation to its zone.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.providers.fake import FakeProvider
from app.triggers.models import TriggerType
from app.triggers.nl_scheduler import NLScheduler, normalize_timezone

NOW = dt.datetime(2026, 10, 6, 0, 49, 12, tzinfo=dt.UTC)


async def _parse(answer: dict, text: str = "x") -> tuple[list, FakeProvider]:
    provider = FakeProvider(responses=[json.dumps(answer)])
    specs = await NLScheduler(provider=provider).parse(text, now=NOW)
    return specs, provider


@pytest.mark.asyncio
async def test_the_prompt_carries_the_current_time_and_the_iana_rule() -> None:
    _, provider = await _parse({"trigger_type": "cron", "cron_expression": "0 9 * * 1-5"})
    prompt = "\n".join(m.content for m in provider.call_history[0].messages)
    assert "2026-10-06T00:49:12+00:00" in prompt
    assert "IANA" in prompt
    assert "fire_at_iso" in prompt and "interval_seconds" in prompt


@pytest.mark.parametrize(
    ("abbr", "zone"),
    [("IST", "Asia/Kolkata"), ("ist", "Asia/Kolkata"), ("EST", "America/New_York"),
     ("PDT", "America/Los_Angeles"), ("GMT", "UTC"), ("JST", "Asia/Tokyo"),
     ("CET", "Europe/Berlin"), ("Asia/Kolkata", "Asia/Kolkata"), ("", "UTC")],
)
def test_common_abbreviations_map_to_their_zone(abbr: str, zone: str) -> None:
    assert normalize_timezone(abbr) == zone


def test_an_unknown_name_is_left_for_validation_to_refuse() -> None:
    assert normalize_timezone("Mars/Olympus") == "Mars/Olympus"


@pytest.mark.asyncio
async def test_an_abbreviated_zone_from_the_model_is_normalised() -> None:
    specs, _ = await _parse(
        {"trigger_type": "cron", "cron_expression": "30 9 * * 1-5", "timezone": "IST"}
    )
    assert specs[0].timezone == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_a_bare_offset_delay_becomes_a_one_off_at_now_plus_the_offset() -> None:
    specs, _ = await _parse({"trigger_type": "relative_delay", "relative_offset_seconds": 1200})
    assert specs[0].trigger_type == TriggerType.ONCE
    assert specs[0].fire_at_iso == "2026-10-06T01:09:12+00:00"


@pytest.mark.asyncio
async def test_an_event_relative_delay_is_kept() -> None:
    specs, _ = await _parse(
        {"trigger_type": "relative_delay", "relative_offset_seconds": 1800,
         "event_channel": "support.escalated"}
    )
    assert specs[0].trigger_type == TriggerType.RELATIVE_DELAY
    assert specs[0].event_channel == "support.escalated"
    assert specs[0].relative_offset_seconds == 1800


@pytest.mark.asyncio
async def test_calendar_fields_reach_the_spec() -> None:
    specs, _ = await _parse(
        {"trigger_type": "business_calendar", "cron_expression": "0 10 * * *",
         "timezone": "Asia/Kolkata", "holidays": ["2026-11-09"], "catch_up": "latest",
         "deadline_warning_seconds": 0}
    )
    assert specs[0].holidays == ["2026-11-09"]
    assert specs[0].catch_up == "latest"
