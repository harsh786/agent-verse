"""RPA against a real headless Chromium (marker ``browser``) and a local static site.

Playwright used to be absent from the dev venv, so the real Chromium path —
including the SSRF route guard every request goes through — was never exercised.
Run: ``uv run playwright install chromium`` once, then
``uv run pytest -m browser tests/rpa/test_real_browser.py``.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

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
    hits: list[str] = []

    def log_message(self, *_a: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        if self.path == "/":
            body = (
                b"<html><head><title>AgentVerse Test Page</title></head><body>"
                b"<h1 id='greet'>Hello from the local site</h1>"
                b"<a id='meta' href='/to-metadata'>metadata</a>"
                b"<img src='http://127.0.0.1:9/tracker.png'>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def site() -> Iterator[str]:
    _Site.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # "localhost" is the operator allowlist entry that opens this private host.
        yield f"http://localhost:{server.server_address[1]}"
    finally:
        server.shutdown()


def _executor(tmp_path: Path):  # type: ignore[no-untyped-def]
    from app.rpa.artifacts import RPAArtifactStore
    from app.rpa.executor import RPAExecutor
    from app.rpa.session_manager import BrowserSessionManager

    return RPAExecutor(
        session_manager=BrowserSessionManager(),
        artifact_store=RPAArtifactStore(base_dir=tmp_path),
        allowed_domains=["localhost"],
    )


async def test_open_extract_and_screenshot_a_real_page(site: str, tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    try:
        opened = await ex.execute(
            tool_name="rpa_open_url", arguments={"url": site + "/"},
            session_id="s1", tenant_id="t1",
        )
        assert opened.success, opened.error
        assert "AgentVerse Test Page" in opened.output
        text = await ex.execute(
            tool_name="rpa_extract_text", arguments={"selector": "#greet"},
            session_id="s1", tenant_id="t1",
        )
        assert text.success, text.error
        assert "Hello from the local site" in text.output
        shot = await ex.execute(
            tool_name="rpa_screenshot", arguments={}, session_id="s1", tenant_id="t1"
        )
        assert shot.success, shot.error
        assert shot.artifact_name or shot.artifact_url
    finally:
        await ex.aclose()


async def test_redirect_to_cloud_metadata_is_blocked(site: str, tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    try:
        res = await ex.execute(
            tool_name="rpa_open_url", arguments={"url": site + "/to-metadata"},
            session_id="s2", tenant_id="t1",
        )
        page = ex._session_manager.get_page("s2", tenant_id="t1")
        landed = page.url if page is not None else ""
        assert "169.254.169.254" not in landed
        assert not res.success or "169.254.169.254" not in (res.output or "")
    finally:
        await ex.aclose()


async def test_direct_metadata_url_is_refused_before_navigation(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    try:
        res = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "http://169.254.169.254/latest/meta-data/"},
            session_id="s3", tenant_id="t1",
        )
        assert res.success is False
        assert "SSRF" in (res.error or "")
    finally:
        await ex.aclose()


async def test_subresource_to_a_non_allowlisted_private_host_is_aborted(
    site: str, tmp_path: Path
) -> None:
    """The page's <img src=http://127.0.0.1:9/...> is not on the allowlist (only
    'localhost' is): the guard aborts it, the page still loads."""
    ex = _executor(tmp_path)
    try:
        res = await ex.execute(
            tool_name="rpa_open_url", arguments={"url": site + "/"},
            session_id="s4", tenant_id="t1",
        )
        assert res.success, res.error
        page = ex._session_manager.get_page("s4", tenant_id="t1")
        loaded = await page.evaluate(
            "() => Array.from(document.images).map(i => i.complete && i.naturalWidth > 0)"
        )
        assert loaded == [False]
    finally:
        await ex.aclose()
