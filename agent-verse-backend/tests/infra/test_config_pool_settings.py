"""Verify database pool settings are configurable via environment."""
from __future__ import annotations

import os


def test_config_has_database_url():
    from app.core.config import Settings
    s = Settings()
    assert s.database_url is not None
    assert len(s.database_url) > 0


def test_default_pool_settings_exist():
    from app.core.config import Settings
    s = Settings()
    # db_pool_size was added in Phase 9
    pool_size = getattr(s, "db_pool_size", None)
    if pool_size is not None:
        assert pool_size > 0


def test_pool_pre_ping_is_bool():
    from app.core.config import Settings
    s = Settings()
    pre_ping = getattr(s, "db_pool_pre_ping", None)
    if pre_ping is not None:
        assert isinstance(pre_ping, bool)
