"""TRG-08: validation requires exactly what the beat reads, so a stored trigger fires.

relative_delay with only relative_to_field, deadline with only deadline_field and
business_calendar with only business_calendar_id were accepted, but the beat
reads fire_at_iso / cron_expression and never fired them. db_row_change was
accepted for tables outside the (empty-by-default) allowlist, which the beat
refuses to poll.
"""

from __future__ import annotations

import datetime

import pytest

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import creatable_error, validate_spec


@pytest.fixture
def allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "db_row_change_tables", "orders, tickets")


@pytest.mark.parametrize(
    ("spec", "needle"),
    [
        (
            TriggerSpec(trigger_type=TriggerType.RELATIVE_DELAY, relative_to_field="$.created_at"),
            "fire_at_iso",
        ),
        (
            TriggerSpec(
                trigger_type=TriggerType.RELATIVE_DELAY,
                relative_offset_seconds=60,
                fire_at_iso="nope",
            ),
            "ISO",
        ),
        (TriggerSpec(trigger_type=TriggerType.DEADLINE, deadline_field="$.due"), "fire_at_iso"),
        (
            TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR, business_calendar_id="us"),
            "cron_expression",
        ),
        (
            TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR, cron_expression="not cron"),
            "cron",
        ),
        (TriggerSpec(trigger_type=TriggerType.DB_ROW_CHANGE, db_table="users"), "allowlist"),
        (TriggerSpec(trigger_type=TriggerType.DB_ROW_CHANGE, db_table="orders;--"), "allowlist"),
    ],
)
def test_unfireable_configs_are_rejected(
    allowlist: None, spec: TriggerSpec, needle: str
) -> None:
    with pytest.raises(ValueError, match=needle):
        validate_spec(spec, plan="enterprise")


def test_db_row_change_with_no_allowlist_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "db_row_change_tables", "")
    reason = creatable_error(TriggerSpec(trigger_type=TriggerType.DB_ROW_CHANGE, db_table="orders"))
    assert reason is not None and "allowlist" in reason


def test_fireable_configs_pass(allowlist: None) -> None:
    for spec in (
        TriggerSpec(
            trigger_type=TriggerType.RELATIVE_DELAY,
            fire_at_iso="2026-10-01T09:00:00Z",
            relative_offset_seconds=3600,
        ),
        TriggerSpec(trigger_type=TriggerType.DEADLINE, fire_at_iso="2026-10-01T09:00:00"),
        TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR, cron_expression="0 9 * * *"),
        TriggerSpec(trigger_type=TriggerType.DB_ROW_CHANGE, db_table="orders"),
    ):
        validate_spec(spec, plan="enterprise")


def test_allowlist_endpoint_lists_the_pollable_tables(allowlist: None) -> None:
    from fastapi.testclient import TestClient

    from tests.api.test_schedules_api import _VALID_KEY, _make_app

    client = TestClient(_make_app())
    r = client.get("/schedules/db-row-change-tables", headers={"X-API-Key": _VALID_KEY})
    assert r.status_code == 200, r.text
    assert r.json() == {"tables": ["orders", "tickets"]}
    assert client.get("/schedules/db-row-change-tables").status_code == 401


def test_a_valid_relative_delay_still_fires() -> None:
    from app.scaling.tasks import _relative_delay_due_utc

    due = _relative_delay_due_utc(
        "2026-10-01T09:00:00", 3600, datetime.datetime(2026, 10, 1, 10, 0, 0), None
    )
    assert due == datetime.datetime(2026, 10, 1, 10, 0, 0)
