"""Production flags every DSN that still carries a dev-default database password."""

from __future__ import annotations

import logging

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings() -> object:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_production_flags_the_default_app_role_and_owner_passwords(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://agentverse_app:agentverse_app@db:5432/agentverse"
    )
    monkeypatch.setenv(
        "MAINTENANCE_DATABASE_URL", "postgresql+asyncpg://agentverse:agentverse@db:5432/av"
    )
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql+asyncpg://owner:s3cret@db:5432/av")
    with caplog.at_level(logging.ERROR, logger="app.core.config"):
        try:
            get_settings()
        except Exception:  # other production guards may refuse this config too
            pass
    flagged = " ".join(r.getMessage() for r in caplog.records)
    assert "DATABASE_URL contains a default development password" in flagged
    assert "MAINTENANCE_DATABASE_URL contains a default development password" in flagged
    assert "MIGRATION_DATABASE_URL contains" not in flagged
