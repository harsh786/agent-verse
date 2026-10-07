"""Tests for the public status page API (app/api/public_status.py).

Regression: the router was never mounted (/status 404), and it read
``app.state.health_registry`` / ``run_all()`` — neither exists — so it always
said "operational". It now reads the real ``app.state.health`` HealthRegistry.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.public_status import router as status_router
from app.observability.health import HealthCheck, HealthRegistry


async def _ok() -> None:
    return None


async def _down() -> None:
    raise RuntimeError("postgres://user:secret@db/internal refused")


def _make_app(registry: HealthRegistry | None) -> FastAPI:
    app = FastAPI()
    app.include_router(status_router)
    app.state.health = registry
    return app


def _get(app: FastAPI) -> dict:
    resp = TestClient(app, raise_server_exceptions=False).get("/status")
    assert resp.status_code == 200
    return resp.json()


def test_no_checks_is_unknown_not_operational() -> None:
    body = _get(_make_app(HealthRegistry()))
    assert body["status"] == "unknown"
    assert body["components"] == {"api": {"status": "operational"}}
    assert body["page_title"] == "AgentVerse System Status"


def test_no_registry_is_unknown() -> None:
    assert _get(_make_app(None))["status"] == "unknown"


def test_all_healthy_is_operational() -> None:
    reg = HealthRegistry()
    reg.register(HealthCheck("postgres", _ok))
    reg.register(HealthCheck("redis", _ok))
    body = _get(_make_app(reg))
    assert body["status"] == "operational"
    assert body["components"]["postgres"] == {"status": "operational"}


def test_one_down_is_degraded_and_leaks_nothing() -> None:
    reg = HealthRegistry()
    reg.register(HealthCheck("postgres", _down))
    reg.register(HealthCheck("redis", _ok))
    app = _make_app(reg)
    resp = TestClient(app).get("/status")
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["components"]["postgres"] == {"status": "degraded"}
    assert "secret" not in resp.text


def test_failed_router_marks_api_degraded() -> None:
    reg = HealthRegistry()
    reg.register(HealthCheck("postgres", _ok))
    app = _make_app(reg)
    app.state.failed_routers = ["ocr_router"]
    body = _get(app)
    assert body["status"] == "degraded"
    assert body["components"]["api"] == {"status": "degraded"}


def test_status_is_mounted_and_public_on_the_real_app() -> None:
    from app.main import create_app

    app = create_app()
    resp = TestClient(app).get("/status")  # no API key
    assert resp.status_code == 200
    assert resp.json()["status"] in {"operational", "degraded", "unknown"}


# ── a10-F244-01: anonymous GET /status does not run every check every time ──


def test_status_checks_are_cached_and_single_flight(monkeypatch) -> None:
    import asyncio

    import httpx

    monkeypatch.setenv("PUBLIC_STATUS_CACHE_SECONDS", "60")
    runs = 0

    async def _slow_ok() -> None:
        nonlocal runs
        runs += 1
        await asyncio.sleep(0.05)

    reg = HealthRegistry()
    reg.register(HealthCheck("postgres", _slow_ok))
    app = _make_app(reg)

    async def _burst() -> list[dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            resps = await asyncio.gather(*(c.get("/status") for _ in range(20)))
            resps.append(await c.get("/status"))
        return [r.json() for r in resps]

    bodies = asyncio.run(_burst())
    assert runs == 1
    assert all(b["status"] == "operational" for b in bodies)
    assert len({b["timestamp"] for b in bodies}) == 1


def test_status_cache_expires(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_STATUS_CACHE_SECONDS", "0")
    runs = 0

    async def _count() -> None:
        nonlocal runs
        runs += 1

    reg = HealthRegistry()
    reg.register(HealthCheck("redis", _count))
    client = TestClient(_make_app(reg))
    client.get("/status")
    client.get("/status")
    assert runs == 2  # cache disabled: every request checks


def test_status_cache_reflects_a_new_failure_after_the_window(monkeypatch) -> None:
    import app.api.public_status as ps

    monkeypatch.setenv("PUBLIC_STATUS_CACHE_SECONDS", "5")
    now = [1000.0]
    monkeypatch.setattr(ps, "_now", lambda: now[0])
    healthy = [True]

    async def _flip() -> None:
        if not healthy[0]:
            raise RuntimeError("down")

    reg = HealthRegistry()
    reg.register(HealthCheck("postgres", _flip))
    client = TestClient(_make_app(reg))
    assert client.get("/status").json()["status"] == "operational"
    healthy[0] = False
    now[0] += 4
    assert client.get("/status").json()["status"] == "operational"  # still cached
    now[0] += 2
    assert client.get("/status").json()["status"] == "degraded"
