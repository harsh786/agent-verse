"""B1-2: an unknown timezone is refused instead of silently running in UTC.

Live (2026-10-06): POST /triggers with ``timezone="America/New_Yrok"`` answered
201 and the beat evaluated the cron in UTC (``_resolve_tz`` falls back to UTC
for any name it cannot load), hours away from what the user asked for.
"""

from __future__ import annotations

import pytest

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import creatable_error, validate_spec


@pytest.mark.parametrize(
    ("trigger_type", "extra"),
    [
        (TriggerType.CRON, {"cron_expression": "0 9 * * 1-5"}),
        (TriggerType.BUSINESS_CALENDAR, {"cron_expression": "0 10 * * *"}),
    ],
)
@pytest.mark.parametrize("tz", ["America/New_Yrok", "IST", "GMT+5:30", "Mars/Olympus", "../etc"])
def test_unknown_timezone_is_refused(trigger_type: TriggerType, extra: dict, tz: str) -> None:
    spec = TriggerSpec(trigger_type=trigger_type, timezone=tz, **extra)
    with pytest.raises(ValueError, match="timezone"):
        validate_spec(spec, plan="enterprise")
    reason = creatable_error(spec, plan="enterprise")
    assert reason is not None and tz in reason


@pytest.mark.parametrize(
    "tz", ["UTC", "utc", "Asia/Kolkata", "America/New_York", "Europe/London", "Etc/GMT-14"]
)
def test_iana_timezones_are_accepted(tz: str) -> None:
    validate_spec(
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *", timezone=tz),
        plan="enterprise",
    )


def test_empty_timezone_means_utc() -> None:
    validate_spec(
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *", timezone=""),
        plan="enterprise",
    )
