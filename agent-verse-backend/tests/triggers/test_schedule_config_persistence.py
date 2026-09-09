"""Unit coverage: family-specific trigger config survives serialization.

These lock in the fix for the FILE_DROP-never-fires bug: family-specific fields
(file_drop_path, rss_url, poll_url, ...) must round-trip through both schedule
payload serializers and the TriggerSpec -> `config` blob helper.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.triggers.models import TriggerSpec, TriggerType


def test_config_dict_carries_only_non_default_family_fields() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.FILE_DROP,
        file_drop_path="/data/inbox",
        file_pattern="*.csv",
    )

    config = spec.config_dict()

    assert config == {"file_drop_path": "/data/inbox", "file_pattern": "*.csv"}


def test_config_dict_carries_polling_and_rss_fields() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.RSS_FEED,
        rss_url="https://example.com/feed.xml",
    )
    poll_spec = TriggerSpec(
        trigger_type=TriggerType.API_POLL,
        poll_url="https://api.example.com/status",
        poll_interval_seconds=900,
    )

    assert spec.config_dict() == {"rss_url": "https://example.com/feed.xml"}
    assert poll_spec.config_dict() == {
        "poll_url": "https://api.example.com/status",
        "poll_interval_seconds": 900,
    }


def test_config_dict_empty_for_plain_cron_spec() -> None:
    # A cron spec carries no family-specific config, and dataclass defaults
    # (e.g. poll_interval_seconds=300) must NOT leak into the blob.
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="*/5 * * * *")

    assert spec.config_dict() == {}


def test_redis_payload_includes_config_blob() -> None:
    from app.triggers.store import ScheduleStore

    spec = TriggerSpec(
        trigger_type=TriggerType.FILE_DROP,
        file_drop_path="/data/inbox",
        file_pattern="*.json",
    )
    rec = {
        "schedule_id": "sched-1",
        "goal_id": "Ingest dropped files",
        "agent_id": "",
        "goal_template": "",
        "spec": spec,
        "paused": False,
    }

    payload = ScheduleStore._redis_payload(rec, "tenant-1")

    assert payload["config"] == {"file_drop_path": "/data/inbox", "file_pattern": "*.json"}


def test_db_schedule_payload_includes_config_blob() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = SimpleNamespace(
        id="sched-db-1",
        tenant_id="tenant-1",
        agent_id="",
        goal_id_template="Ingest dropped files",
        trigger_type="file_drop",
        cron_expression="",
        timezone="UTC",
        interval_seconds=0,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        condition="",
        description="",
        paused=False,
        last_fired_at=None,
        config={"file_drop_path": "/data/inbox", "file_pattern": "*.pdf"},
    )

    payload = _db_schedule_payload(row)

    assert payload["config"] == {"file_drop_path": "/data/inbox", "file_pattern": "*.pdf"}


def test_db_schedule_payload_config_defaults_to_empty_dict() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = SimpleNamespace(
        id="sched-db-2",
        tenant_id="tenant-1",
        agent_id="",
        goal_id_template="cron goal",
        trigger_type="cron",
        cron_expression="*/5 * * * *",
        timezone="UTC",
        interval_seconds=0,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        condition="",
        description="",
        paused=False,
        last_fired_at=None,
        config=None,
    )

    payload = _db_schedule_payload(row)

    assert payload["config"] == {}
