"""Browser agent — headless Chromium automation via Playwright.

Provides web automation capabilities when no API is available:
- Navigate to URLs
- Take screenshots → analyze with vision LLM
- Click elements, type text, scroll
- Extract text content

Egress: every navigation target AND every request the page makes (redirect hops,
subresources) is checked with the DNS-resolving SSRF guard; non-public hosts
(loopback, RFC-1918, link-local/metadata) are refused. Previously only an
``http(s)://`` prefix was checked, so a caller could screenshot/extract
http://169.254.169.254/ or an internal service, directly or via a redirect.
Automatic cleanup after each session and timeout enforcement (default 30s
per action) are in place.
"""

from __future__ import annotations

import base64
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


async def _guard_route(route: Any) -> None:
    """Playwright route handler: abort any request to a non-public host."""
    url = str(route.request.url)
    if url.startswith(("http://", "https://")) and await _blocked_reason(url):
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def _guarded_context(browser: Any, **kwargs: Any) -> Any:
    import inspect

    context = await browser.new_context(**kwargs)
    registered = context.route("**/*", _guard_route)
    if inspect.isawaitable(registered):
        await registered
    return context


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

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await _guarded_context(
                    browser,
                    viewport={"width": 1280, "height": 720},
                )
                page = await context.new_page()
                page.set_default_timeout(self._timeout)
                await page.goto(url, wait_until="domcontentloaded")
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                return BrowserResult(
                    success=True,
                    action="screenshot",
                    output=f"Screenshot taken of {url}",
                    screenshot_b64=screenshot_b64,
                )
            except Exception as exc:
                return BrowserResult(success=False, action="screenshot", error=str(exc))
            finally:
                await browser.close()

    async def extract_text(self, url: str, selector: str = "body") -> BrowserResult:
        """Extract visible text from a URL."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(
                success=False, action="extract_text", error="Playwright not installed"
            )
        if reason := await _blocked_reason(url):
            return BrowserResult(success=False, action="extract_text", error=reason)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await (await _guarded_context(browser)).new_page()
                page.set_default_timeout(self._timeout)
                await page.goto(url, wait_until="domcontentloaded")
                text = await page.inner_text(selector)
                return BrowserResult(
                    success=True,
                    action="extract_text",
                    output=text[:5000],  # Truncate long content
                )
            except Exception as exc:
                return BrowserResult(success=False, action="extract_text", error=str(exc))
            finally:
                await browser.close()

    async def click_and_screenshot(self, url: str, selector: str) -> BrowserResult:
        """Navigate to URL, click element, return screenshot."""
        if not _PLAYWRIGHT_AVAILABLE:
            return BrowserResult(success=False, action="click", error="Playwright not installed")
        if reason := await _blocked_reason(url):
            return BrowserResult(success=False, action="click", error=reason)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await (await _guarded_context(browser)).new_page()
                page.set_default_timeout(self._timeout)
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
            finally:
                await browser.close()

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

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await (await _guarded_context(browser)).new_page()
                page.set_default_timeout(self._timeout)
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
            finally:
                await browser.close()

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
            resp = await self._vision.complete(req)
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
