"""Regression: family-specific trigger config must survive persistence.

``ScheduleStore.sync_from_db`` rebuilt a ``TriggerSpec`` from the core Schedule
columns but never read back ``schedules.config``, so file-watch/RSS/S3/Sheets/
price-alert config was silently dropped on restart or cross-replica sync (blank
in the schedule API), even though the beat firing path reads it from the same
column. ``spec_config`` also persisted only a narrow subset of the ~90 family
fields. These tests pin the persist → rehydrate round-trip.
"""
from __future__ import annotations

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import apply_config_to_spec, spec_config


def _roundtrip(spec: TriggerSpec) -> TriggerSpec:
    cfg = spec_config(spec)
    fresh = TriggerSpec(trigger_type=spec.trigger_type)
    apply_config_to_spec(fresh, cfg)
    return fresh


def test_bare_spec_persists_no_config_noise():
    # A spec with no family config must produce an empty config dict — no defaults.
    assert spec_config(TriggerSpec(trigger_type=TriggerType.ONCE)) == {}


def test_file_drop_config_survives_roundtrip():
    # file_drop_path is remapped to file_watch_path on persist; it must map back.
    spec = TriggerSpec(trigger_type=TriggerType.ONCE, file_drop_path="/data/in")
    assert _roundtrip(spec).file_drop_path == "/data/in"


def test_wide_family_config_survives_roundtrip():
    spec = TriggerSpec(
        trigger_type=TriggerType.ONCE,
        rss_url="https://example.com/feed",
        poll_url="https://example.com/api",
        s3_bucket="my-bucket",
        s3_prefix="incoming/",
        sheets_spreadsheet_id="ss-1",
        sharepoint_site_url="https://sp",
        github_event_filter="push",
        db_table="orders",
    )
    out = _roundtrip(spec)
    assert out.rss_url == "https://example.com/feed"
    assert out.poll_url == "https://example.com/api"
    assert out.s3_bucket == "my-bucket"
    assert out.s3_prefix == "incoming/"
    assert out.sheets_spreadsheet_id == "ss-1"
    assert out.sharepoint_site_url == "https://sp"
    assert out.github_event_filter == "push"
    assert out.db_table == "orders"


def test_secrets_and_core_columns_never_persisted_in_config():
    spec = TriggerSpec(
        trigger_type=TriggerType.WEBHOOK,
        webhook_token="tok",
        webhook_signature_secret="s3cr3t",
        cron_expression="* * * * *",
        rss_url="https://example.com/feed",
    )
    cfg = spec_config(spec)
    for leaked in ("webhook_signature_secret", "webhook_token", "cron_expression", "trigger_type"):
        assert leaked not in cfg
    assert cfg.get("rss_url") == "https://example.com/feed"


def test_apply_config_is_robust_to_junk():
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    # None, unknown keys, and bad-typed values must never raise.
    apply_config_to_spec(spec, None)
    apply_config_to_spec(spec, {"not_a_field": 1, "cron_expression": object()})
    apply_config_to_spec(spec, "not a dict")  # type: ignore[arg-type]
