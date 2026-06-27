"""Verify RedBeat scheduler configuration is correctly wired."""
from __future__ import annotations

import os


def test_celery_app_has_beat_schedule():
    """Beat schedule must be a dict with at least one task."""
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/test.db")

    from app.scaling.celery_app import celery_app
    beat_schedule = getattr(celery_app.conf, "beat_schedule", {}) or {}
    assert isinstance(beat_schedule, dict)
    assert len(beat_schedule) > 0


def test_celery_app_redbeat_scheduler():
    """Beat scheduler should be RedBeat when configured."""
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

    from app.scaling.celery_app import celery_app
    scheduler = getattr(celery_app.conf, "beat_scheduler", None)
    # Either not set (uses default) OR set to RedBeat
    if scheduler is not None:
        assert "redbeat" in str(scheduler).lower()
