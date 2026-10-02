"""OAUTH-03: no boot-time bulk token load; per-process OAuth caches are bounded."""

from __future__ import annotations

import inspect

import pytest

import app.mcp.oauth as oauth_mod
from app.mcp.oauth import OAuthFlowManager, OAuthToken


def test_lifespan_does_not_bulk_load_every_tenants_tokens() -> None:
    import app.main as main_mod

    source = inspect.getsource(main_mod.create_app)
    assert "load_tokens_from_db(" not in source


def test_token_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_mod, "_TOKEN_CACHE_MAX", 50)
    mgr = OAuthFlowManager()
    mgr._db_session_factory = object()  # durable mode: memory is only a cache
    for i in range(200):
        mgr._cache_token((f"t{i}", "srv"), OAuthToken(access_token=f"at{i}"))
    assert len(mgr._tokens) == 50
    assert len(mgr._token_cached_at) == 50
    # Most recent entries are the ones kept.
    assert ("t199", "srv") in mgr._tokens and ("t0", "srv") not in mgr._tokens


def test_refresh_locks_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_mod, "_TOKEN_CACHE_MAX", 50)
    mgr = OAuthFlowManager()
    for i in range(200):
        mgr._get_refresh_lock(f"t{i}", "srv")
    assert len(mgr._refresh_locks) <= 50


@pytest.mark.asyncio
async def test_a_held_refresh_lock_is_never_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_mod, "_TOKEN_CACHE_MAX", 5)
    mgr = OAuthFlowManager()
    held = mgr._get_refresh_lock("t-held", "srv")
    async with held:
        for i in range(50):
            mgr._get_refresh_lock(f"t{i}", "srv")
        assert mgr._get_refresh_lock("t-held", "srv") is held
