"""Perception against a real headless Chromium (marker ``browser``) and a local page.

The real path (shared browser, guarded contexts, one load per URL) was only ever
mock-tested. Needs ``uv run playwright install chromium``.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

import app.perception.browser_agent as mod

pytestmark = pytest.mark.browser


def _chromium_ready() -> bool:
    try:
        import asyncio

        from app.rpa import readiness

        asyncio.run(readiness.check_browser_available())
        return True
    except Exception:
        return False


if not _chromium_ready():  # pragma: no cover - environment dependent
    pytest.skip("Playwright Chromium not installed", allow_module_level=True)


class _Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:
        pass

    def do_GET(self) -> None:
        body = (
            b"<html><head><title>Perception Page</title></head>"
            b"<body><main>Quarterly numbers are up</main></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Perception is public-only by design; open just this local test host.
    async def _local_ok(url: str) -> str:
        return "" if url.startswith(f"http://localhost:{server.server_address[1]}") else "blocked"

    async def _ctx(browser, **kwargs):  # type: ignore[no-untyped-def]
        from app.net.browser_guard import new_guarded_context

        return await new_guarded_context(
            browser, allowed_domains=["localhost"], context="perception test", **kwargs
        )

    monkeypatch.setattr(mod, "_blocked_reason", _local_ok)
    monkeypatch.setattr(mod, "_guarded_context", _ctx)
    monkeypatch.setattr(mod, "_SHARED", mod._SharedBrowser())
    try:
        yield f"http://localhost:{server.server_address[1]}/"
    finally:
        server.shutdown()


async def test_capture_screenshot_and_text_in_one_real_page_load(site: str) -> None:
    agent = mod.BrowserAgent()
    try:
        shot, text = await agent.capture(site)
        assert shot.success, shot.error
        assert shot.screenshot_b64
        assert text.success and "Quarterly numbers are up" in text.output
        again = await agent.extract_text(site, "main")
        assert again.success and again.output == "Quarterly numbers are up"
        assert mod._SHARED.launches == 1  # both actions shared one Chromium
    finally:
        await mod.aclose_shared_browser()


async def test_metadata_url_is_refused_by_the_guard_in_a_real_browser(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _public_only(url: str) -> str:
        return ""  # skip the pre-check: the in-browser guard must still stop it

    monkeypatch.setattr(mod, "_blocked_reason", _public_only)
    agent = mod.BrowserAgent(timeout_ms=10_000)
    try:
        res = await agent.extract_text("http://169.254.169.254/latest/meta-data/")
        assert res.success is False
    finally:
        await mod.aclose_shared_browser()
