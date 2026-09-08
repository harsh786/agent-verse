"""2.W-1: family-specific schedule config must reach the beat loop.

Previously the beat branches read fields (file_watch_path, rss_url, poll_url)
that neither schedule-payload serializer carried and the schedules table had no
columns for — so FILE_DROP never fired and RSS_FEED could not be added. These
tests pin the plumbing: spec_config maps the fields, and both the Redis payload
and the DB payload surface them at the top level the beat loop reads.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, spec_config


def test_spec_config_maps_file_drop_path_to_watch_path() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.FILE_DROP, file_drop_path="/data/inbox")
    cfg = spec_config(spec)
    assert cfg["file_watch_path"] == "/data/inbox"
    assert cfg["file_pattern"] == "*"


def test_spec_config_maps_rss_url() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.RSS_FEED, rss_url="https://ex.com/feed.xml")
    assert spec_config(spec) == {"rss_url": "https://ex.com/feed.xml"}


def test_spec_config_maps_poll_fields() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.API_POLL,
        poll_url="https://ex.com/api",
        poll_jsonpath="$.status",
        poll_expected_value="ok",
    )
    cfg = spec_config(spec)
    assert cfg["poll_url"] == "https://ex.com/api"
    assert cfg["poll_method"] == "GET"
    assert cfg["poll_jsonpath"] == "$.status"


def test_spec_config_empty_for_core_types() -> None:
    assert (
        spec_config(TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")) == {}
    )


def test_redis_payload_surfaces_family_fields() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.RSS_FEED, rss_url="https://ex.com/feed.xml")
    rec = {"schedule_id": "s1", "spec": spec, "goal_id": "g1", "goal_template": "t"}
    payload = ScheduleStore._redis_payload(rec, "tenant-1")
    assert payload["rss_url"] == "https://ex.com/feed.xml"
    assert payload["trigger_type"] == "rss_feed"


def test_db_payload_merges_config_column() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = SimpleNamespace(
        id="s2",
        tenant_id="tenant-1",
        goal_id_template="do it",
        agent_id="",
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
        config={"file_watch_path": "/data/inbox", "file_pattern": "*.csv"},
    )
    payload = _db_schedule_payload(row)
    assert payload["file_watch_path"] == "/data/inbox"
    assert payload["file_pattern"] == "*.csv"


def test_db_payload_tolerates_missing_config() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = SimpleNamespace(
        id="s3",
        tenant_id="t",
        goal_id_template="x",
        trigger_type="cron",
        cron_expression="0 9 * * *",
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
    assert payload["trigger_type"] == "cron"
    assert "file_watch_path" not in payload
