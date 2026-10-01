"""The API lifespan must bind fast regardless of tenant count.

Measured on the dev stack (~390 tenants): uvicorn only bound after the lifespan
finished, and the lifespan ran ``register_builtin_servers`` for every tenant
(~325 Redis GETs each — 76 s in Docker, 3 min from the host), then seeded the
marketplace and hydrated execution memory inline. Per-tenant built-in rows are
provisioned lazily now (first listing), and non-essential warm-ups run as
tracked background tasks that gate ``GET /health/ready`` instead of blocking
the server from serving.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.startup import StartupTracker
from app.main import create_app
from app.mcp.registry import MCPRegistry
from app.mcp.servers import registry_wiring
from app.services.tenant_service import TenantService

pytestmark = pytest.mark.asyncio

N_TENANTS = 400


class _FakePools:
    def __init__(self) -> None:
        self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        await self.redis.aclose()

    def health_checks(self) -> list[Any]:
        return []


@pytest.fixture
def many_tenants(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sync(self: TenantService) -> int:
        for i in range(N_TENANTS):
            tid = f"tenant-{i:04d}"
            self._tenants[tid] = {
                "tenant_id": tid,
                "name": tid,
                "email": f"{tid}@example.com",
                "plan": "free",
                "created_at": "",
            }
        return N_TENANTS

    monkeypatch.setattr(TenantService, "sync_from_db", _sync)


async def test_lifespan_with_400_tenants_serves_fast_without_per_tenant_mcp_work(
    many_tenants: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    per_tenant_calls: list[str] = []
    real = registry_wiring.register_builtin_servers

    async def _spy(registry: Any, tenant_ctx: Any, **kwargs: Any) -> int:
        per_tenant_calls.append(tenant_ctx.tenant_id)
        return await real(registry, tenant_ctx, **kwargs)

    monkeypatch.setattr(registry_wiring, "register_builtin_servers", _spy)
    app = create_app(settings=Settings(voice_enabled=False), manage_pools=True, pools=_FakePools())

    started = time.monotonic()
    async with app.router.lifespan_context(app):
        startup_seconds = time.monotonic() - started
        tracker: StartupTracker = app.state.startup
        assert tracker.essential_done
        # No per-tenant built-in provisioning at boot ...
        assert per_tenant_calls == []
        # ... but every built-in handler is registered process-wide (no I/O).
        assert MCPRegistry.get_builtin_handler("builtin-utility") is not None
        # Non-essential warm-ups were started in the background, tracked.
        tasks = tracker.snapshot()["tasks"]
        assert "marketplace_v2_seed" in tasks
        # MEM-06: execution memory is read per tenant under RLS on demand — the
        # RLS-blind startup hydration is gone.
        assert "execution_memory_hydration" not in tasks
        assert "goal_warm_cache" in tasks
        await tracker.wait_ready(timeout=60)

    # Generous for slow CI: with fakes this is ~1 s now vs ~11 s with the old loop.
    assert startup_seconds < 25, f"lifespan took {startup_seconds:.1f}s for {N_TENANTS} tenants"


async def test_ready_endpoint_is_503_until_gating_tasks_finish() -> None:
    app = create_app()
    tracker = StartupTracker()
    app.state.startup = tracker
    release = asyncio.Event()
    tracker.spawn("slow_warmup", release.wait)
    tracker.mark_essential_done()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        pending = await client.get("/health/ready")
        assert pending.status_code == 503
        assert pending.json()["status"] == "starting"
        assert pending.json()["pending"] == ["slow_warmup"]
        # Liveness/dependency health stays fast and green meanwhile.
        assert (await client.get("/health")).status_code == 200

        release.set()
        await tracker.wait_ready(timeout=5)
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"


async def test_ready_endpoint_before_essential_phase_is_503() -> None:
    app = create_app()
    app.state.startup = StartupTracker()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/health/ready")
    assert resp.status_code == 503


async def test_failed_background_task_does_not_hold_readiness_forever() -> None:
    tracker = StartupTracker()

    async def _boom() -> None:
        raise RuntimeError("seed failed")

    tracker.spawn("seed", _boom)
    tracker.mark_essential_done()
    assert await tracker.wait_ready(timeout=5)
    assert tracker.snapshot()["tasks"]["seed"]["status"] == "failed"
