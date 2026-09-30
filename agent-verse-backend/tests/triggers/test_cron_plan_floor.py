"""TRG-16: cron / interval schedules respect a per-plan minimum interval.

validate_cron promised a plan floor but only parsed the expression, so any
tenant could create an every-minute cron; and when croniter failed to import
any string was accepted.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from app.triggers.models import (
    PLAN_MIN_SCHEDULE_INTERVAL_SECONDS,
    TriggerSpec,
    TriggerType,
    min_cron_gap_seconds,
    validate_cron,
)
from app.triggers.validation import validate_spec


def test_every_minute_cron_is_refused_on_free() -> None:
    with pytest.raises(ValueError, match="free plan allows"):
        validate_cron("* * * * *", "free")


def test_every_minute_cron_is_allowed_on_enterprise() -> None:
    validate_cron("* * * * *", "enterprise")


def test_the_floor_catches_a_short_gap_hidden_in_a_daily_cron() -> None:
    assert min_cron_gap_seconds("0,1 9 * * *") == 60
    with pytest.raises(ValueError, match="every 1 min"):
        validate_cron("0,1 9 * * *", "free")


def test_a_cron_at_the_floor_passes() -> None:
    floor = PLAN_MIN_SCHEDULE_INTERVAL_SECONDS["free"]
    assert floor % 60 == 0
    validate_cron(f"*/{floor // 60} * * * *", "free")


def test_interval_below_the_plan_floor_is_refused() -> None:
    with pytest.raises(ValueError, match="free plan allows"):
        validate_spec(
            TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60), plan="free"
        )
    validate_spec(
        TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60), plan="enterprise"
    )


def test_missing_croniter_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "croniter":
            raise ImportError("no croniter")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ValueError, match="croniter"):
        validate_cron("0 9 * * *", "enterprise")
