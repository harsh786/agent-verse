"""Browser agent — headless Chromium automation via Playwright.

Provides web automation capabilities when no API is available:
- Navigate to URLs
- Take screenshots → analyze with vision LLM
- Click elements, type text, scroll
- Extract text content

Egress: every navigation target AND every request the page makes (redirect hops,
subresources) is checked with the DNS-resolving SSRF guard: cloud metadata /
link-local are refused, private hosts follow ALLOW_PRIVATE_NETWORK_ACCESS. Previously only an
``http(s)://`` prefix was checked, so a caller could screenshot/extract
http://169.254.169.254/ or an internal service, directly or via a redirect.
Automatic cleanup after each session and timeout enforcement (default 30s
per action) are in place.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.warning(
        "Playwright not installed. Browser agent disabled. Run: playwright install chromium"
    )


async def _blocked_reason(url: str) -> str:
    """Empty string when ``url`` is a public http(s) URL, else the reason."""
    from app.net.ssrf_guard import SSRFError, assert_public_url_async

    try:
        await assert_public_url_async(url, context="perception browser")
    except (SSRFError, ValueError) as exc:
        return f"blocked by SSRF guard: {exc}"
    return ""


async def _guarded_context(browser: Any, **kwargs: Any) -> Any:
    """A browser context whose every request is SSRF-checked.

    Shared with the RPA executor (app.net.browser_guard). The previous local
    route handler used ``route.continue_()``, which lets Chromium follow redirect
    hops unrouted (Playwright does not intercept redirects), and passed
    ``file:``/other schemes through; WebSockets and service workers were
    unguarded too.
    """
    from app.net.browser_guard import new_guarded_context

    return await new_guarded_context(browser, context="perception browser", **kwargs)


class _SharedBrowser:
    """One long-lived Chromium per process (per event loop) with a bounded page pool.

    Every perception action used to launch its own Chromium (a 10-URL batch
    started 20 at once, with no cap), so a few tenants could exhaust a
    replica's memory/CPU. Pages now share one browser; each action gets a fresh
    SSRF-guarded context (isolation) and at most
    ``perception_max_concurrent_pages`` run at once — the rest wait.
    """

    def __init__(self) -> None:
        self._pw: Any = None
        self._pw_cm: Any = None
        self._browser: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._sem: asyncio.Semaphore | None = None
        self.launches = 0

    async def _ensure(self, headless: bool) -> Any:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # Playwright objects belong to one loop; a new loop gets its own.
            self._loop, self._pw, self._browser = loop, None, None
            self._lock = asyncio.Lock()
            from app.core.config import get_settings

            self._sem = asyncio.Semaphore(
                max(1, int(get_settings().perception_max_concurrent_pages))
            )
        assert self._lock is not None
        async with self._lock:
            connected = getattr(self._browser, "is_connected", None)
            if self._browser is None or (callable(connected) and not connected()):
                if self._pw is None:
                    # __aenter__ is PlaywrightContextManager.start(); kept for aclose.
                    self._pw_cm = async_playwright()
                    self._pw = await self._pw_cm.__aenter__()
                self._browser = await self._pw.chromium.launch(headless=headless)
                self.launches += 1
        return self._browser

    @contextlib.asynccontextmanager
    async def page(
        self, *, headless: bool, timeout_ms: int, **context_kwargs: Any
    ) -> AsyncIterator[Any]:
        browser = await self._ensure(headless)
        assert self._sem is not None
        async with self._sem:
            context = await _guarded_context(browser, **context_kwargs)
            try:
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await context.close()

    async def aclose(self) -> None:
        browser, cm = self._browser, self._pw_cm
        self._browser = self._pw = self._pw_cm = None
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
        if cm is not None:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)


_SHARED = _SharedBrowser()


async def aclose_shared_browser() -> None:
    """Close the process's shared perception browser (app shutdown)."""
    await _SHARED.aclose()


@dataclass
class BrowserAction:
    action_type: str  # navigate | click | type | scroll | screenshot | extract_text
    selector: str = ""
    value: str = ""
    url: str = ""


@dataclass
class BrowserResult:
    success: bool
    action: str
    output: str = ""
    screenshot_b64: str = ""  # Base64-encoded PNG screenshot
    error: str = ""


class BrowserAgent:
    """Headless Chromium browser automation agent.

    Each session gets an isolated browser context. Screenshots are analyzed
    by a vision LLM to extract semantic information.
    """

    def __init__(
        self,
        *,
        vision_provider: Any = None,  # Optional LLMProvider with supports_vision()
        timeout_ms: int = 30_000,
        headless: bool = True,
    ) -> None:
        self._vision = vision_provider
        self._timeout = timeout_ms
        self._headless = headless

    @property
    def available(self) -> bool:
        return _PLAYWRIGHT_AVAILABLE

    async def take_screenshot(self, url: str) -> BrowserResult:
        """Navigate to URL and return a base64-encoded screenshot."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(
                success=False, action="screenshot", error="Playwright not installed"
            )
        if reason := await _blocked_reason(url):
            return BrowserResult(success=False, action="screenshot", error=reason)

        try:
            async with _SHARED.page(
                headless=self._headless,
                timeout_ms=self._timeout,
                viewport={"width": 1280, "height": 720},
            ) as page:
                await page.goto(url, wait_until="domcontentloaded")
                screenshot_bytes = await page.screenshot(full_page=False)
            return BrowserResult(
                success=True,
                action="screenshot",
                output=f"Screenshot taken of {url}",
                screenshot_b64=base64.b64encode(screenshot_bytes).decode(),
            )
        except Exception as exc:
            return BrowserResult(success=False, action="screenshot", error=str(exc))

    async def extract_text(self, url: str, selector: str = "body") -> BrowserResult:
        """Extract visible text from a URL."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(
                success=False, action="extract_text", error="Playwright not installed"
            )
        if reason := await _blocked_reason(url):
            return BrowserResult(success=False, action="extract_text", error=reason)

        try:
            async with _SHARED.page(headless=self._headless, timeout_ms=self._timeout) as page:
                await page.goto(url, wait_until="domcontentloaded")
                text = await page.inner_text(selector)
            return BrowserResult(
                success=True,
                action="extract_text",
                output=text[:5000],  # Truncate long content
            )
        except Exception as exc:
            return BrowserResult(success=False, action="extract_text", error=str(exc))

    async def capture(
        self, url: str, *, screenshot: bool = True, text: bool = True, selector: str = "body"
    ) -> tuple[BrowserResult, BrowserResult]:
        """Screenshot and text from ONE page load (screenshot result, text result).

        analyze_url used to load the page twice (take_screenshot + extract_text),
        two Chromium launches per URL.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            err = "Playwright not installed"
            return (
                BrowserResult(success=False, action="screenshot", error=err),
                BrowserResult(success=False, action="extract_text", error=err),
            )
        if reason := await _blocked_reason(url):
            return (
                BrowserResult(success=False, action="screenshot", error=reason),
                BrowserResult(success=False, action="extract_text", error=reason),
            )
        shot = BrowserResult(success=False, action="screenshot", error="not requested")
        txt = BrowserResult(success=False, action="extract_text", error="not requested")
        try:
            async with _SHARED.page(
                headless=self._headless,
                timeout_ms=self._timeout,
                viewport={"width": 1280, "height": 720},
            ) as page:
                await page.goto(url, wait_until="domcontentloaded")
                if screenshot:
                    raw = await page.screenshot(full_page=False)
                    shot = BrowserResult(
                        success=True,
                        action="screenshot",
                        output=f"Screenshot taken of {url}",
                        screenshot_b64=base64.b64encode(raw).decode(),
                    )
                if text:
                    content = await page.inner_text(selector)
                    txt = BrowserResult(success=True, action="extract_text", output=content[:5000])
        except Exception as exc:
            if screenshot and not shot.success:
                shot = BrowserResult(success=False, action="screenshot", error=str(exc))
            if text and not txt.success:
                txt = BrowserResult(success=False, action="extract_text", error=str(exc))
        return shot, txt

    async def click_and_screenshot(self, url: str, selector: str) -> BrowserResult:
        """Navigate to URL, click element, return screenshot."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="click", error="Playwright not installed")
        if reason := await _blocked_reason(url):
            return BrowserResult(success=False, action="click", error=reason)

        try:
            async with _SHARED.page(headless=self._headless, timeout_ms=self._timeout) as page:
                await page.goto(url, wait_until="domcontentloaded")
                await page.click(selector)
                await page.wait_for_load_state("networkidle", timeout=5000)
                screenshot_bytes = await page.screenshot()
            return BrowserResult(
                success=True,
                action="click",
                output=f"Clicked {selector} on {url}",
                screenshot_b64=base64.b64encode(screenshot_bytes).decode(),
            )
        except Exception as exc:
            return BrowserResult(success=False, action="click", error=str(exc))

    async def fill_and_submit(
        self,
        url: str,
        selector: str,
        value: str,
        submit_selector: str = "",
    ) -> BrowserResult:
        """Fill a form field and optionally submit."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="fill", error="Playwright not installed")
        if reason := await _blocked_reason(url):
            return BrowserResult(success=False, action="fill", error=reason)

        try:
            async with _SHARED.page(headless=self._headless, timeout_ms=self._timeout) as page:
                await page.goto(url, wait_until="domcontentloaded")
                await page.fill(selector, value)
                if submit_selector:
                    await page.click(submit_selector)
                    await page.wait_for_load_state("networkidle", timeout=5000)
                screenshot_bytes = await page.screenshot()
            return BrowserResult(
                success=True,
                action="fill",
                output=f"Filled {selector} with value",
                screenshot_b64=base64.b64encode(screenshot_bytes).decode(),
            )
        except Exception as exc:
            return BrowserResult(success=False, action="fill", error=str(exc))

    @property
    def has_vision(self) -> bool:
        """True when a vision-capable provider is configured."""
        return self._vision is not None and bool(self._vision.supports_vision())

    async def analyze_screenshot(
        self, screenshot_b64: str, question: str, *, raise_errors: bool = False
    ) -> str:
        """Analyze a screenshot with a vision LLM.

        With ``raise_errors`` a missing provider or a provider failure raises
        instead of being returned as if it were the analysis text (API callers
        used to answer 200 with "No vision provider configured." as the result).
        """
        if not self.has_vision:
            if raise_errors:
                raise RuntimeError("No vision provider configured.")
            return "No vision provider configured."

        try:
            from app.providers.base import CompletionRequest, Message
            from app.providers.model_defaults import (
                configured_vision_model as _configured_vision_model,
            )

            req = CompletionRequest(
                messages=[
                    Message(
                        role="system", content="You are a web page analyzer. Describe what you see."
                    ),
                    Message(
                        role="user",
                        content=question,
                        image_data=screenshot_b64,
                    ),
                ],
                model=_configured_vision_model("claude-opus-4-5"),
            )
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            resp = await complete_decision(
                self._vision,
                req,
                role="browser_vision",
                timeout_seconds=generation_timeout_seconds(),
            )
            return resp.content  # type: ignore[no-any-return]
        except Exception as exc:
            if raise_errors:
                raise
            return f"Vision analysis failed: {exc}"

    async def run_action(self, action: BrowserAction) -> BrowserResult:
        """Dispatch a browser action."""
        if action.action_type in {"navigate", "screenshot"}:
            return await self.take_screenshot(action.url)
        if action.action_type == "extract_text":
            return await self.extract_text(action.url, action.selector or "body")
        if action.action_type == "click":
            return await self.click_and_screenshot(action.url, action.selector)
        if action.action_type == "fill":
            return await self.fill_and_submit(action.url, action.selector, action.value)
        return BrowserResult(
            success=False,
            action=action.action_type,
            error=f"Unknown action: {action.action_type}",
        )
