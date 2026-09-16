"""Verify RedBeat scheduler configuration is correctly wired."""
from __future__ import annotations

import os

import pytest


def _set_default_env(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    """``os.environ.setdefault`` scoped to the current test.

    A plain ``os.environ.setdefault(...)`` call here previously leaked
    ``DATABASE_URL=sqlite+aiosqlite:///...`` into every later test in the
    process: since this file never reset it, ANY subsequent test that (re)reads
    ``app.core.config.get_settings()`` after its lru_cache is cleared (e.g. by
    ``isolate_provider_env``) picked up this test's throwaway sqlite URL instead
    of the real default — and since the ``aiosqlite`` driver isn't installed,
    any code building a real async engine from that URL blew up with
    ``ModuleNotFoundError: No module named 'aiosqlite'`` (this broke
    ``tests/workflow/test_worker_runner.py::test_get_runner_builds_worker_runner_when_state_is_in_memory``
    order-dependently). Using ``monkeypatch.setenv`` scopes the env var to this
    test only, and clearing the settings cache makes sure no test after this one
    ever sees a ``Settings`` object built from the polluted env.
    """
    if key not in os.environ:
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _clear_settings_cache_after():
    yield
    try:
        from app.core.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def test_celery_app_has_beat_schedule(monkeypatch: pytest.MonkeyPatch):
    """Beat schedule must be a dict with at least one task."""
    _set_default_env(monkeypatch, "REDIS_URL", "redis://localhost:6379/0")
    _set_default_env(monkeypatch, "DATABASE_URL", "sqlite+aiosqlite:////tmp/test.db")

    from app.scaling.celery_app import celery_app
    beat_schedule = getattr(celery_app.conf, "beat_schedule", {}) or {}
    assert isinstance(beat_schedule, dict)
    assert len(beat_schedule) > 0


def test_celery_app_redbeat_scheduler(monkeypatch: pytest.MonkeyPatch):
    """Beat scheduler should be RedBeat when configured."""
    _set_default_env(monkeypatch, "REDIS_URL", "redis://localhost:6379/0")

    from app.scaling.celery_app import celery_app
    scheduler = getattr(celery_app.conf, "beat_scheduler", None)
    # Either not set (uses default) OR set to RedBeat
    if scheduler is not None:
        assert "redbeat" in str(scheduler).lower()
