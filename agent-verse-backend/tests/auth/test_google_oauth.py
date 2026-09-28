"""Tests for the Google OIDC login/callback flow (app/auth/google_oauth.py)."""
from __future__ import annotations

import sys
import time
import types
from unittest.mock import AsyncMock

import httpx as _httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import google_oauth
from app.auth.google_oauth import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _clear_pkce_store():
    google_oauth._pkce_store.clear()
    yield
    google_oauth._pkce_store.clear()


@pytest.fixture(autouse=True)
def _configure_oauth(monkeypatch):
    monkeypatch.setattr(google_oauth, "_CLIENT_ID", "client-123")
    monkeypatch.setattr(google_oauth, "_CLIENT_SECRET", "secret-abc")
    monkeypatch.setattr(google_oauth, "_REDIRECT_URI", "http://localhost:8000/auth/google/callback")


class TestGeneratePkce:
    def test_verifier_and_challenge_differ_each_call(self):
        v1, c1 = google_oauth._generate_pkce()
        v2, c2 = google_oauth._generate_pkce()
        assert v1 != v2
        assert c1 != c2
        # base64url, no padding
        assert "=" not in c1
        assert isinstance(v1, str) and isinstance(c1, str)


class TestPkceStore:
    async def test_set_and_pop_in_memory_roundtrip(self):
        await google_oauth._pkce_store_set("state1", {"verifier": "v"})
        data = await google_oauth._pkce_store_pop("state1")
        assert data == {"verifier": "v"}
        # popped — gone now
        assert await google_oauth._pkce_store_pop("state1") is None

    async def test_pop_missing_state_returns_none(self):
        assert await google_oauth._pkce_store_pop("no-such-state") is None

    async def test_set_uses_redis_when_available(self):
        redis = AsyncMock()
        await google_oauth._pkce_store_set("state-r", {"verifier": "v"}, redis=redis)
        redis.set.assert_awaited_once()
        # still falls back to in-memory store too
        assert "state-r" in google_oauth._pkce_store

    async def test_set_suppresses_redis_errors(self):
        redis = AsyncMock()
        redis.set.side_effect = RuntimeError("boom")
        await google_oauth._pkce_store_set("state-err", {"verifier": "v"}, redis=redis)
        # in-memory fallback still recorded despite redis failure
        assert google_oauth._pkce_store["state-err"] == {"verifier": "v"}

    async def test_pop_uses_redis_hit(self):
        redis = AsyncMock()
        redis.get.return_value = '{"verifier": "from-redis"}'
        data = await google_oauth._pkce_store_pop("state-r2", redis=redis)
        assert data == {"verifier": "from-redis"}
        redis.delete.assert_awaited_once()

    async def test_pop_redis_miss_falls_back_to_memory(self):
        redis = AsyncMock()
        redis.get.return_value = None
        google_oauth._pkce_store["state-mem"] = {"verifier": "mem"}
        data = await google_oauth._pkce_store_pop("state-mem", redis=redis)
        assert data == {"verifier": "mem"}

    async def test_pop_redis_exception_falls_back_to_memory(self):
        redis = AsyncMock()
        redis.get.side_effect = RuntimeError("down")
        google_oauth._pkce_store["state-exc"] = {"verifier": "mem2"}
        data = await google_oauth._pkce_store_pop("state-exc", redis=redis)
        assert data == {"verifier": "mem2"}


class TestGoogleLogin:
    def test_login_not_configured_returns_503(self, monkeypatch):
        monkeypatch.setattr(google_oauth, "_CLIENT_ID", "")
        client = TestClient(_make_app())
        resp = client.get("/auth/google/login", follow_redirects=False)
        assert resp.status_code == 503

    def test_login_redirects_to_google_with_pkce_params(self):
        client = TestClient(_make_app())
        resp = client.get("/auth/google/login", follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert location.startswith(google_oauth._GOOGLE_AUTH_URL)
        assert "code_challenge=" in location
        assert "code_challenge_method=S256" in location
        assert "client_id=client-123" in location
        # state was stored for later retrieval
        assert len(google_oauth._pkce_store) == 1


class TestGoogleCallback:
    def test_invalid_state_returns_400(self):
        client = TestClient(_make_app())
        resp = client.get(
            "/auth/google/callback", params={"code": "abc", "state": "unknown-state"}
        )
        assert resp.status_code == 400
        assert "state" in resp.json()["detail"].lower()

    def test_token_exchange_failure_returns_400(self):
        google_oauth._pkce_store["st1"] = {"verifier": "v1", "created_at": time.time()}
        client = TestClient(_make_app())
        with respx.mock:
            respx.post(google_oauth._GOOGLE_TOKEN_URL).mock(
                return_value=_httpx.Response(400, json={"error": "invalid_grant"})
            )
            resp = client.get(
                "/auth/google/callback", params={"code": "abc", "state": "st1"}
            )
        assert resp.status_code == 400
        assert "token exchange failed" in resp.json()["detail"].lower()

    def test_userinfo_failure_returns_400(self):
        google_oauth._pkce_store["st2"] = {"verifier": "v2", "created_at": time.time()}
        client = TestClient(_make_app())
        with respx.mock:
            respx.post(google_oauth._GOOGLE_TOKEN_URL).mock(
                return_value=_httpx.Response(200, json={"access_token": "tok"})
            )
            respx.get(google_oauth._GOOGLE_USERINFO_URL).mock(
                return_value=_httpx.Response(401, json={"error": "invalid_token"})
            )
            resp = client.get(
                "/auth/google/callback", params={"code": "abc", "state": "st2"}
            )
        assert resp.status_code == 400
        assert "user info" in resp.json()["detail"].lower()

    def test_missing_email_returns_400(self):
        google_oauth._pkce_store["st3"] = {"verifier": "v3", "created_at": time.time()}
        client = TestClient(_make_app())
        with respx.mock:
            respx.post(google_oauth._GOOGLE_TOKEN_URL).mock(
                return_value=_httpx.Response(200, json={"access_token": "tok"})
            )
            respx.get(google_oauth._GOOGLE_USERINFO_URL).mock(
                return_value=_httpx.Response(200, json={"sub": "12345"})
            )
            resp = client.get(
                "/auth/google/callback", params={"code": "abc", "state": "st3"}
            )
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()

    # The callback used to import a non-existent app.auth.jwt_service, swallow
    # the ImportError and answer 200 with a "note" — and, when the upsert failed,
    # derive a tenant_id from the email's local part. It now never fakes a login.

    def _callback(self, app, state: str, userinfo: dict):
        google_oauth._pkce_store[state] = {"verifier": "v", "created_at": time.time()}
        client = TestClient(app)
        with respx.mock:
            respx.post(google_oauth._GOOGLE_TOKEN_URL).mock(
                return_value=_httpx.Response(200, json={"access_token": "tok"})
            )
            respx.get(google_oauth._GOOGLE_USERINFO_URL).mock(
                return_value=_httpx.Response(200, json=userinfo)
            )
            return client.get("/auth/google/callback", params={"code": "abc", "state": state})

    def test_unverified_email_is_rejected(self):
        resp = self._callback(
            _make_app(), "st4", {"email": "u@example.com", "sub": "s", "email_verified": False}
        )
        assert resp.status_code == 400
        assert "not verified" in resp.json()["detail"]
        assert "st4" not in google_oauth._pkce_store  # PKCE state consumed

    def test_without_db_is_503_not_a_fake_success(self):
        resp = self._callback(
            _make_app(), "st5", {"email": "u@example.com", "sub": "s", "email_verified": True}
        )
        assert resp.status_code == 503

    def test_upsert_failure_is_503_and_never_derives_a_tenant_from_email(self, monkeypatch):
        async def _raise_upsert(*, db_factory, email, google_sub, name, picture_url):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.auth.user_service.upsert_google_user", _raise_upsert)
        app = _make_app()
        app.state.db_session_factory = object()
        resp = self._callback(
            app, "st6", {"email": "u3@example.com", "sub": "sub-3", "email_verified": True}
        )
        assert resp.status_code == 503
        assert "u3" not in resp.text

    def test_verified_user_gets_honest_not_implemented(self, monkeypatch):
        async def _fake_upsert(*, db_factory, email, google_sub, name, picture_url):
            return ("user-42", "tenant-42")

        monkeypatch.setattr("app.auth.user_service.upsert_google_user", _fake_upsert)
        fake_module = types.ModuleType("app.auth.jwt_service")
        fake_module.mint_jwt = lambda **_: "forged"
        monkeypatch.setitem(sys.modules, "app.auth.jwt_service", fake_module)
        app = _make_app()
        app.state.db_session_factory = object()
        resp = self._callback(
            app, "st7", {"email": "u2@example.com", "sub": "sub-2", "email_verified": True}
        )
        assert resp.status_code == 501
        data = resp.json()
        assert data["error"] == "NOT_IMPLEMENTED"
        assert data["user_id"] == "user-42"
        assert data["tenant_id"] == "tenant-42"
        assert "access_token" not in data
