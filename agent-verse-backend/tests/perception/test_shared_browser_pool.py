"""PERC-01: one shared Chromium per process, a bounded page pool, one page load per
URL, and a per-tenant concurrent-page cap shared through Redis.

Every perception action launched its own Chromium; a 10-URL batch started 20 at
once with no cap, so a few tenants could exhaust a replica's memory/CPU.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.perception.browser_agent as mod
from app.core.config import get_settings
from app.perception.browser_agent import BrowserAgent, BrowserResult
from app.tenancy.context import PlanTier, TenantContext
from tests.rpa._lease_redis import LeaseRedis


class _FakePlaywright:
    """async_playwright() stand-in counting launches and concurrent pages."""

    def __init__(self, page_delay: float = 0.05) -> None:
        self.launches = 0
        self.open_pages = 0
        self.peak_pages = 0
        self.gotos: list[str] = []
        self._delay = page_delay

    def __call__(self) -> Any:
        fake = self

        class _CM:
            async def __aenter__(self) -> Any:
                pw = MagicMock()

                async def _launch(**_kw: Any) -> Any:
                    fake.launches += 1
                    browser = MagicMock()
                    browser.is_connected = MagicMock(return_value=True)
                    browser.close = AsyncMock()
                    return browser

                pw.chromium.launch = _launch
                return pw

            async def __aexit__(self, *_a: Any) -> None:
                return None

        return _CM()

    async def new_context(self, _browser: Any, **_kw: Any) -> Any:
        fake = self
        ctx = MagicMock()

        async def _new_page() -> Any:
            fake.open_pages += 1
            fake.peak_pages = max(fake.peak_pages, fake.open_pages)
            page = MagicMock()
            page.set_default_timeout = MagicMock()

            async def _goto(url: str, **_k: Any) -> None:
                fake.gotos.append(url)
                await asyncio.sleep(fake._delay)

            page.goto = _goto
            page.screenshot = AsyncMock(return_value=b"png")
            page.inner_text = AsyncMock(return_value="page text")
            return page

        async def _close() -> None:
            fake.open_pages -= 1

        ctx.new_page = _new_page
        ctx.close = _close
        return ctx


@pytest.fixture
def fake_pw(monkeypatch: pytest.MonkeyPatch) -> _FakePlaywright:
    fake = _FakePlaywright()
    monkeypatch.setattr(mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(mod, "async_playwright", fake, raising=False)
    monkeypatch.setattr(mod, "_guarded_context", fake.new_context)
    monkeypatch.setattr(mod, "_SHARED", mod._SharedBrowser())

    async def _public(_url: str) -> str:
        return ""

    monkeypatch.setattr(mod, "_blocked_reason", _public)
    monkeypatch.setenv("PERCEPTION_MAX_CONCURRENT_PAGES", "2")
    get_settings.cache_clear()
    yield fake
    get_settings.cache_clear()


async def test_one_browser_and_bounded_concurrent_pages(fake_pw: _FakePlaywright) -> None:
    agent = BrowserAgent()
    results = await asyncio.gather(
        *(agent.take_screenshot(f"https://example.com/{i}") for i in range(6))
    )
    assert all(r.success for r in results)
    assert fake_pw.launches == 1  # one Chromium, not six
    assert fake_pw.peak_pages == 2  # bounded by perception_max_concurrent_pages
    assert fake_pw.open_pages == 0  # every context closed


async def test_analyze_url_loads_the_page_once(fake_pw: _FakePlaywright) -> None:
    from app.perception.page_analyzer import PageAnalyzer

    analysis = await PageAnalyzer(browser_agent=BrowserAgent()).analyze_url(
        "https://example.com/a"
    )
    assert analysis.success
    assert analysis.screenshot_b64 and analysis.text_content == "page text"
    assert fake_pw.gotos == ["https://example.com/a"]


def _app(redis: Any) -> TestClient:
    from app.api.perception import router

    ctx = TenantContext(tenant_id="t-perc", plan=PlanTier.FREE, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    agent = BrowserAgent()
    agent.take_screenshot = AsyncMock(  # type: ignore[method-assign]
        return_value=BrowserResult(success=True, action="screenshot", screenshot_b64="x")
    )
    app.state.browser_agent = agent
    app.state._redis = redis
    return TestClient(app)


def test_tenant_page_cap_is_shared_through_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.perception as api

    monkeypatch.setenv("PERCEPTION_MAX_PAGES_PER_TENANT", "1")
    get_settings.cache_clear()

    async def _public(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(api, "_require_public_url", _public)
    try:
        redis = LeaseRedis()
        # Another replica already holds the tenant's only slot.
        redis.zsets["perception:leases:t-perc"] = {"other-replica:0": 1e18}
        resp = _app(redis).post("/perception/screenshot", json={"url": "https://example.com"})
        assert resp.status_code == 429
        redis.zsets["perception:leases:t-perc"] = {}
        ok = _app(redis).post("/perception/screenshot", json={"url": "https://example.com"})
        assert ok.status_code == 200
        assert redis.zsets["perception:leases:t-perc"] == {}  # released after the page
    finally:
        get_settings.cache_clear()


def test_batch_larger_than_the_tenant_cap_is_429(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.perception as api

    monkeypatch.setenv("PERCEPTION_MAX_PAGES_PER_TENANT", "2")
    get_settings.cache_clear()

    async def _public(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(api, "_require_public_url", _public)
    try:
        client = _app(LeaseRedis())
        client.app.state.browser_agent._vision = MagicMock(supports_vision=lambda: True)
        resp = client.post(
            "/perception/batch-analyze",
            json={"urls": [f"https://example.com/{i}" for i in range(3)]},
        )
        assert resp.status_code == 429
    finally:
        get_settings.cache_clear()
