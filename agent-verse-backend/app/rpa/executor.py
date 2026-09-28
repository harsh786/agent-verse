"""RPA executor — executes browser automation commands via Playwright or simulation fallback."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# WS-13: tools that can produce REAL page text over a plain HTTP GET when no
# browser (Playwright) is installed. Everything else fails closed with a
# NOT IMPLEMENTED error (a click/type without a browser has no real effect).
_HTTP_FETCH_TOOLS = frozenset({"rpa_open_url", "rpa_extract_text", "rpa_screenshot"})

_KNOWN_RPA_TOOLS = frozenset(
    {
        "rpa_open_url",
        "rpa_click",
        "rpa_type",
        "rpa_extract_text",
        "rpa_screenshot",
        "rpa_wait_for_text",
        "rpa_select_option",
        "rpa_upload_file",
        "rpa_download_file",
        "rpa_submit_form",
        "rpa_detect_captcha",
        "rpa_request_human_help",
        "rpa_wait_for_network_idle",
    }
)

# Heuristic markers of common CAPTCHA widgets, checked against the live DOM.
_CAPTCHA_MARKERS = (
    "g-recaptcha",
    "recaptcha/api",
    "h-captcha",
    "hcaptcha.com",
    "cf-turnstile",
    "challenges.cloudflare.com",
    "arkoselabs",
    "funcaptcha",
)

_DEFAULT_UPLOAD_ROOT = "/tmp/agentverse-rpa-uploads"


def tenant_upload_dir(tenant_id: str) -> Any:
    """The only directory ``rpa_upload_file`` may read from for ``tenant_id``.

    ``<RPA_UPLOAD_DIR or /tmp/agentverse-rpa-uploads>/<tenant_id>/``.
    """
    import os
    from pathlib import Path

    from app.rpa.artifacts import _safe_path_component

    root = Path(os.getenv("RPA_UPLOAD_DIR") or _DEFAULT_UPLOAD_ROOT)
    return root / _safe_path_component(tenant_id, default="_")


def resolve_upload_path(tenant_id: str, file_path: str) -> tuple[str, str]:
    """Map an agent-supplied ``file_path`` into the tenant's upload dir.

    Returns ``(resolved_path, "")`` or ``("", error)``. ``rpa_upload_file`` used
    to pass ANY host path to ``set_input_files`` — an agent (reachable by prompt
    injection) could upload /etc/passwd, the app's .env or another tenant's
    artifacts to an attacker's page. Only relative paths inside the tenant's
    upload dir are accepted: absolute paths, ``..`` and symlink escapes are
    refused.
    """
    from pathlib import Path, PurePosixPath, PureWindowsPath

    if not tenant_id:
        return "", "rpa_upload_file requires a tenant context"
    raw = file_path.replace("\\", "/")
    if (
        PurePosixPath(raw).is_absolute()
        or PureWindowsPath(file_path).is_absolute()
        or PureWindowsPath(file_path).drive
        or ".." in PurePosixPath(raw).parts
    ):
        return "", (
            "file_path must be a relative path inside the tenant upload directory "
            "(absolute paths and '..' are not allowed)"
        )
    base = Path(tenant_upload_dir(tenant_id)).resolve()
    candidate = (base / raw).resolve()
    if not candidate.is_relative_to(base):
        return "", "file_path escapes the tenant upload directory"
    if not candidate.is_file():
        return "", f"File not found: {file_path}"
    return str(candidate), ""


@dataclass
class RPAResult:
    success: bool
    output: str = ""
    artifact_url: str | None = None  # base64 screenshot data URI or storage URI
    artifact_name: str | None = None
    duration_ms: float = 0.0
    error: str | None = None


class RPAExecutor:
    """Executes RPA tool calls via Playwright; without a browser it fails closed."""

    def __init__(
        self,
        artifact_store: Any = None,
        session_manager: Any = None,
        headless: bool = True,
        vision_provider: Any = None,
        allowed_domains: list[str] | None = None,
        secret_store_resolver: Callable[[], Any] | None = None,
    ) -> None:
        self._playwright_available = self._check_playwright()
        # Returns the tenant-aware connector secret store (read lazily so the
        # lifespan's Redis-backed swap on app.state takes effect).
        self._secret_store_resolver = secret_store_resolver
        self._headless = headless
        self._artifact_store = artifact_store
        self._session_manager = session_manager
        self._vision_provider = vision_provider
        # SSRF egress allowlist: exact domains (and their subdomains) that may be
        # navigated even if they would otherwise resolve to an internal address.
        # Empty by default → public-only (metadata/loopback/RFC-1918 all blocked).
        self._allowed_domains = allowed_domains
        # P1.2: Vault credential injector (set externally or at construction time)
        self._credential_injector: Any = None
        # WS-13: per-session cache of page text fetched via the httpx fallback so
        # an ``open_url`` → ``extract_text`` sequence sharing a session id returns
        # the page it actually fetched (mirrors Playwright session page state).
        self._http_pages: dict[str, str] = {}

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str | None = None,
        tenant_id: str = "",
        goal_id: str = "",
        allow_http_fetch: bool = False,
    ) -> RPAResult:
        """Execute an RPA tool command.

        ``allow_http_fetch`` opts a caller into the WS-13 real-HTTP fallback: when
        no browser (Playwright) is installed, ``rpa_open_url``/``rpa_extract_text``
        fetch the page over httpx and return its REAL text instead of the
        ``[simulated]`` placeholder. Off by default so existing simulation
        behaviour (and its tests) is preserved.
        """
        start = time.monotonic()
        sid = session_id or uuid.uuid4().hex
        ephemeral = session_id is None

        # P1.2: Resolve vault:// credential references before dispatching to Playwright.
        # A per-call, tenant-scoped injector is built from the app's connector
        # secret store whenever the arguments carry a vault:// reference (the
        # injector used to be declared but never constructed anywhere).
        injector = self._credential_injector
        if injector is None:
            from app.rpa.credential_injector import CredentialInjector, contains_vault_ref

            if contains_vault_ref(arguments):
                store = self._secret_store_resolver() if self._secret_store_resolver else None
                injector = CredentialInjector(secret_store=store, tenant_id=tenant_id)
        if injector is not None:
            try:
                arguments = await injector.resolve_arguments(arguments)
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("credential_injection_failed error=%s", exc)
                # Fail closed: continuing would type the raw ``vault://`` reference
                # into the page (or run a login step without its secret).
                return RPAResult(
                    success=False,
                    error=f"credential injection failed: {exc}",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

        # SSRF egress guard: validate the target URL before ANY real navigation
        # or fetch. Both Playwright paths call page.goto(url) and the WS-13 http
        # fallback issues a real GET — all reachable from an agent's rpa_* tool
        # call, so an attacker-supplied url like http://169.254.169.254/… or a
        # loopback/RFC-1918 host would otherwise reach internal services. The
        # simulation path makes no request and is intentionally exempt (its tests
        # use arbitrary placeholder URLs). Fail-closed on any block.
        _will_fetch = self._playwright_available or (
            allow_http_fetch and tool_name in _HTTP_FETCH_TOOLS
        )
        _target_url = arguments.get("url", "")
        if _will_fetch and _target_url:
            from app.net.ssrf_guard import SSRFError, assert_public_url

            try:
                assert_public_url(
                    _target_url,
                    allowed_domains=self._allowed_domains,
                    context="rpa_navigate",
                )
            except (SSRFError, ValueError) as exc:
                return RPAResult(
                    success=False,
                    error=f"blocked by SSRF guard: {exc}",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

        if self._playwright_available and self._session_manager:
            result = await self._execute_with_playwright(
                tool_name=tool_name,
                arguments=arguments,
                session_id=sid,
                tenant_id=tenant_id,
                goal_id=goal_id,
            )
        elif self._playwright_available:
            result = await self._execute_playwright_standalone(
                tool_name=tool_name,
                arguments=arguments,
                goal_id=goal_id,
                tenant_id=tenant_id,
            )
        elif allow_http_fetch and tool_name in _HTTP_FETCH_TOOLS:
            result = await self._execute_http_fallback(
                tool_name=tool_name, arguments=arguments, session_id=sid
            )
        else:
            result = await self._execute_simulation(tool_name=tool_name, arguments=arguments)

        if ephemeral and self._session_manager:
            await self._session_manager.close(sid, tenant_id)

        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    async def _execute_with_playwright(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        tenant_id: str,
        goal_id: str,
    ) -> RPAResult:
        """Execute using a stateful Playwright session from session_manager."""
        session = await self._session_manager.get_or_create(session_id, tenant_id)
        page = session.page

        if page is None:
            return await self._execute_simulation(tool_name=tool_name, arguments=arguments)

        try:
            url = arguments.get("url", "")

            if tool_name == "rpa_open_url":
                if not url:
                    return RPAResult(success=False, error="url argument required")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                session.current_url = url
                session.touch()
                return RPAResult(
                    success=True,
                    output=f"Navigated to {url} — title: {await page.title()}",
                )

            elif tool_name == "rpa_click":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                selector = arguments.get("selector", "")
                text = arguments.get("text", "")
                try:
                    if text and not selector:
                        await page.get_by_text(text, exact=False).first.click(timeout=5000)
                    elif selector:
                        await page.click(selector, timeout=5000)
                    else:
                        return RPAResult(success=False, error="selector or text required")
                    screenshot = base64.b64encode(await page.screenshot()).decode()
                    session.touch()
                    return RPAResult(
                        success=True,
                        output=f"Clicked: {selector or text}",
                        artifact_url=f"data:image/png;base64,{screenshot}",
                    )
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_type":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                selector = arguments.get("selector", "")
                text_to_type = arguments.get("text", "")
                if not selector:
                    return RPAResult(success=False, error="selector required for rpa_type")
                try:
                    await page.fill(selector, text_to_type, timeout=5000)
                    session.touch()
                    return RPAResult(success=True, output=f"Typed into {selector}")
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_extract_text":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                selector = arguments.get("selector", "body")
                try:
                    text = await page.inner_text(selector, timeout=5000)
                    session.touch()
                    return RPAResult(success=True, output=text[:5000])
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_screenshot":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                name = arguments.get("name", "screenshot")
                screenshot_bytes = await page.screenshot()
                b64 = base64.b64encode(screenshot_bytes).decode()

                # Persist to artifact store if available
                artifact_url = f"data:image/png;base64,{b64}"
                artifact_name = f"{name}.png"

                if self._artifact_store and goal_id:
                    try:
                        artifact = await self._artifact_store.write_bytes(
                            goal_id=goal_id,
                            name=artifact_name,
                            content=screenshot_bytes,
                        )
                        artifact_url = artifact.uri
                        artifact_name = artifact.name
                    except Exception:
                        pass  # Fall back to base64 in response

                # Analyze screenshot with vision provider if available
                vision_analysis = ""
                if self._vision_provider:
                    try:
                        from app.perception.browser_agent import BrowserAgent

                        _ba = BrowserAgent(vision_provider=self._vision_provider)
                        vision_analysis = await _ba.analyze_screenshot(
                            b64,
                            "Describe the main content and purpose of this page.",
                        )
                    except Exception:
                        pass

                output = f"Screenshot captured: {name}"
                if vision_analysis:
                    output += f"\nVision analysis: {vision_analysis}"

                session.touch()
                return RPAResult(
                    success=True,
                    output=output,
                    artifact_url=artifact_url,
                    artifact_name=artifact_name,
                )

            elif tool_name == "rpa_wait_for_text":
                text = arguments.get("text", "")
                if not text:
                    return RPAResult(success=False, error="text argument required")
                timeout = int(arguments.get("timeout_ms", 10000))
                try:
                    locator = page.get_by_text(text, exact=False)
                    await locator.wait_for(timeout=timeout)
                    session.touch()
                    return RPAResult(
                        success=True,
                        output=f"Text '{text}' appeared on page",
                    )
                except Exception:
                    content = await page.content()
                    if text in content:
                        session.touch()
                        return RPAResult(
                            success=True,
                            output=f"Text '{text}' found in page content",
                        )
                    return RPAResult(
                        success=False,
                        error=f"Text '{text}' did not appear within {timeout}ms",
                    )

            elif tool_name == "rpa_select_option":
                selector = arguments.get("selector", "")
                value = arguments.get("value", "")
                if not selector:
                    return RPAResult(success=False, error="selector argument required")
                try:
                    selected = await page.select_option(selector, value=value)
                    if not selected:
                        selected = await page.select_option(selector, label=value)
                    session.touch()
                    return RPAResult(
                        success=True,
                        output=f"Selected '{value}' in element '{selector}'",
                    )
                except Exception as exc:
                    return RPAResult(
                        success=False,
                        error=f"Could not select '{value}' in '{selector}': {exc}",
                    )

            elif tool_name == "rpa_upload_file":
                import os

                selector = arguments.get("selector", "")
                file_path = arguments.get("file_path", "")
                if not selector:
                    return RPAResult(success=False, error="selector argument required")
                if not file_path:
                    return RPAResult(success=False, error="file_path argument required")
                # Confined to the tenant's upload dir (was: any host path).
                safe_path, path_error = resolve_upload_path(tenant_id, str(file_path))
                if path_error:
                    return RPAResult(success=False, error=path_error)
                try:
                    await page.set_input_files(selector, safe_path)
                    filename = os.path.basename(safe_path)
                    session.touch()
                    return RPAResult(
                        success=True,
                        output=f"Uploaded file '{filename}' to '{selector}'",
                    )
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_download_file":
                selector = arguments.get("selector", "")
                if not selector:
                    return RPAResult(success=False, error="selector argument required")
                result = await self._download_via_click(page, selector, goal_id=goal_id)
                session.touch()
                return result

            elif tool_name == "rpa_submit_form":
                field_values: dict = arguments.get("field_values", {})
                submit_selector = arguments.get("submit_selector", "button[type=submit]")
                filled: list[str] = []
                try:
                    for sel, value in field_values.items():
                        element = page.locator(sel)
                        tag = await element.evaluate("el => el.tagName.toLowerCase()")
                        input_type = await element.evaluate("el => el.type || ''")
                        if tag == "select":
                            await page.select_option(sel, value=str(value))
                        elif input_type in ("checkbox", "radio"):
                            if value:
                                await element.check()
                            else:
                                await element.uncheck()
                        else:
                            await element.fill(str(value))
                        filled.append(sel)
                    try:
                        await page.click(submit_selector)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        await page.keyboard.press("Enter")
                    session.touch()
                    return RPAResult(
                        success=True,
                        output=f"Filled {len(filled)} fields and submitted form",
                    )
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))

            elif tool_name == "rpa_wait_for_network_idle":
                timeout_ms = int(arguments.get("timeout_ms", 10000))
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                session.touch()
                return RPAResult(success=True, output=f"Network idle (timeout: {timeout_ms}ms)")

            elif tool_name == "rpa_detect_captcha":
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    session.current_url = url
                found = await self._detect_captcha(page)
                session.touch()
                return RPAResult(
                    success=True,
                    output=f"captcha_detected: {'true' if found else 'false'}"
                    + (f" ({', '.join(found)})" if found else ""),
                )

            else:
                return await self._execute_simulation(tool_name=tool_name, arguments=arguments)

        except Exception as exc:
            return RPAResult(success=False, error=str(exc))

    async def _execute_playwright_standalone(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        goal_id: str = "",
        tenant_id: str = "",
    ) -> RPAResult:
        """Execute using a short-lived Playwright browser (no session manager).

        All 5 RPA tools are fully supported. Browser is opened and closed per call.
        For stateful multi-step workflows, use execute() with a session_id instead.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return RPAResult(
                success=False,
                error=f"NOT IMPLEMENTED: {tool_name} requires a real browser "
                "(Playwright is not installed)",
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="AgentVerse-RPA/1.0",
                )
                page = await context.new_page()

                url = arguments.get("url", "")

                if tool_name == "rpa_open_url":
                    if not url:
                        return RPAResult(success=False, error="url argument required")
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    return RPAResult(
                        success=True,
                        output=f"Opened {url} — title: {await page.title()}",
                    )

                elif tool_name == "rpa_click":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    selector = arguments.get("selector", "")
                    text = arguments.get("text", "")
                    if text and not selector:
                        await page.get_by_text(text, exact=False).first.click(timeout=5000)
                    elif selector:
                        await page.click(selector, timeout=5000)
                    else:
                        return RPAResult(
                            success=False,
                            error="selector or text required for rpa_click",
                        )
                    screenshot = base64.b64encode(await page.screenshot()).decode()
                    return RPAResult(
                        success=True,
                        output=f"Clicked: {selector or text}",
                        artifact_url=f"data:image/png;base64,{screenshot}",
                    )

                elif tool_name == "rpa_type":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    selector = arguments.get("selector", "")
                    text_to_type = arguments.get("text", "")
                    if not selector:
                        return RPAResult(
                            success=False,
                            error="selector required for rpa_type",
                        )
                    await page.fill(selector, text_to_type, timeout=5000)
                    return RPAResult(success=True, output=f"Typed into {selector}")

                elif tool_name == "rpa_extract_text":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    selector = arguments.get("selector", "body")
                    text = await page.inner_text(selector, timeout=5000)
                    return RPAResult(success=True, output=text[:5000])

                elif tool_name == "rpa_screenshot":
                    if url:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    name = arguments.get("name", "screenshot")
                    screenshot_bytes = await page.screenshot()
                    b64 = base64.b64encode(screenshot_bytes).decode()

                    artifact_url = f"data:image/png;base64,{b64}"
                    artifact_name = f"{name}.png"

                    if self._artifact_store and goal_id:
                        try:
                            artifact = await self._artifact_store.write_bytes(
                                goal_id=goal_id,
                                name=artifact_name,
                                content=screenshot_bytes,
                            )
                            artifact_url = artifact.uri
                            artifact_name = artifact.name
                        except Exception:
                            pass

                    return RPAResult(
                        success=True,
                        output=f"Screenshot: {name}",
                        artifact_url=artifact_url,
                        artifact_name=artifact_name,
                    )

                elif tool_name == "rpa_wait_for_text":
                    text = arguments.get("text", "")
                    if not text:
                        return RPAResult(success=False, error="text argument required")
                    timeout = int(arguments.get("timeout_ms", 10000))
                    try:
                        locator = page.get_by_text(text, exact=False)
                        await locator.wait_for(timeout=timeout)
                        return RPAResult(
                            success=True,
                            output=f"Text '{text}' appeared on page",
                        )
                    except Exception:
                        content = await page.content()
                        if text in content:
                            return RPAResult(
                                success=True,
                                output=f"Text '{text}' found in page content",
                            )
                        return RPAResult(
                            success=False,
                            error=f"Text '{text}' did not appear within {timeout}ms",
                        )

                elif tool_name == "rpa_select_option":
                    selector = arguments.get("selector", "")
                    value = arguments.get("value", "")
                    if not selector:
                        return RPAResult(success=False, error="selector argument required")
                    try:
                        selected = await page.select_option(selector, value=value)
                        if not selected:
                            selected = await page.select_option(selector, label=value)
                        return RPAResult(
                            success=True,
                            output=f"Selected '{value}' in element '{selector}'",
                        )
                    except Exception as exc:
                        return RPAResult(
                            success=False,
                            error=f"Could not select '{value}' in '{selector}': {exc}",
                        )

                elif tool_name == "rpa_upload_file":
                    import os

                    selector = arguments.get("selector", "")
                    file_path = arguments.get("file_path", "")
                    if not selector:
                        return RPAResult(success=False, error="selector argument required")
                    if not file_path:
                        return RPAResult(success=False, error="file_path argument required")
                    # Confined to the tenant's upload dir (was: any host path).
                    safe_path, path_error = resolve_upload_path(tenant_id, str(file_path))
                    if path_error:
                        return RPAResult(success=False, error=path_error)
                    try:
                        await page.set_input_files(selector, safe_path)
                        filename = os.path.basename(safe_path)
                        return RPAResult(
                            success=True,
                            output=f"Uploaded file '{filename}' to '{selector}'",
                        )
                    except Exception as exc:
                        return RPAResult(success=False, error=str(exc))

                elif tool_name == "rpa_download_file":
                    selector = arguments.get("selector", "")
                    if not selector:
                        return RPAResult(success=False, error="selector argument required")
                    result = await self._download_via_click(page, selector, goal_id=goal_id)
                    return result

                elif tool_name == "rpa_submit_form":
                    field_values: dict = arguments.get("field_values", {})
                    submit_selector = arguments.get("submit_selector", "button[type=submit]")
                    filled: list[str] = []
                    try:
                        for sel, value in field_values.items():
                            element = page.locator(sel)
                            tag = await element.evaluate("el => el.tagName.toLowerCase()")
                            input_type = await element.evaluate("el => el.type || ''")
                            if tag == "select":
                                await page.select_option(sel, value=str(value))
                            elif input_type in ("checkbox", "radio"):
                                if value:
                                    await element.check()
                                else:
                                    await element.uncheck()
                            else:
                                await element.fill(str(value))
                            filled.append(sel)
                        try:
                            await page.click(submit_selector)
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            await page.keyboard.press("Enter")
                        return RPAResult(
                            success=True,
                            output=f"Filled {len(filled)} fields and submitted form",
                        )
                    except Exception as exc:
                        return RPAResult(success=False, error=str(exc))

                else:
                    return await self._execute_simulation(tool_name=tool_name, arguments=arguments)

            except Exception as exc:
                return RPAResult(success=False, error=str(exc))
            finally:
                await browser.close()

    @staticmethod
    async def _detect_captcha(page: Any) -> list[str]:
        """Return the CAPTCHA markers present in the live DOM (empty if none)."""
        html = (await page.content()).lower()
        frame_urls = " ".join(str(getattr(f, "url", "")) for f in page.frames).lower()
        haystack = html + " " + frame_urls
        return [m for m in _CAPTCHA_MARKERS if m in haystack]

    async def _download_via_click(self, page: Any, selector: str, *, goal_id: str) -> RPAResult:
        """Click ``selector``, capture the download and persist it as an artifact.

        Previously this called ``artifact_store.store_bytes`` (which no store
        implements), swallowed the AttributeError, deleted the temp file and still
        returned ``success=True`` with the deleted host temp path as the artifact
        URL. Now the bytes go through the store's real ``write_bytes`` (sync or
        async), the temp file never outlives the call, and any failure — including
        having no artifact store to put the file in — is reported as a failure.
        """
        import inspect
        import os
        import tempfile

        if self._artifact_store is None:
            return RPAResult(
                success=False,
                error="rpa_download_file: no artifact store configured to persist the download",
            )
        tmp_path = ""
        try:
            async with page.expect_download() as download_info:
                await page.click(selector)
            download = await download_info.value
            filename = str(download.suggested_filename or "download.bin")
            with tempfile.NamedTemporaryFile(delete=False, suffix="_rpa_download") as f:
                tmp_path = f.name
            await download.save_as(tmp_path)
            with open(tmp_path, "rb") as f:
                content = f.read()
            written = self._artifact_store.write_bytes(
                goal_id=goal_id or "rpa-adhoc", name=filename, content=content
            )
            if inspect.isawaitable(written):
                written = await written
            return RPAResult(
                success=True,
                output=f"Downloaded '{filename}' ({len(content)} bytes)",
                artifact_url=str(getattr(written, "uri", "") or ""),
                artifact_name=str(getattr(written, "name", "") or filename),
            )
        except Exception as exc:
            return RPAResult(success=False, error=f"rpa_download_file failed: {exc}")
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    async def _execute_http_fallback(
        self, *, tool_name: str, arguments: dict[str, Any], session_id: str
    ) -> RPAResult:
        """WS-13: real page text over httpx when no browser is installed.

        This is the *no-Playwright* production path for the KB scraper — it fetches
        the page and returns its actual text (not a ``[simulated]`` placeholder),
        so routing ``/knowledge/ingest/rpa-url`` through the RPA executor no longer
        regresses the browser-less path. Screenshots are not possible over httpx,
        so ``rpa_screenshot`` degrades to a no-op success (text still flows).
        """
        if tool_name == "rpa_open_url":
            url = arguments.get("url", "")
            if not url:
                return RPAResult(success=False, error="url argument required")
            try:
                text, title = await self._http_fetch_text(url)
            except Exception as exc:
                return RPAResult(success=False, error=str(exc))
            self._http_pages[session_id] = text
            return RPAResult(success=True, output=f"Opened {url} — title: {title}")

        if tool_name == "rpa_extract_text":
            url = arguments.get("url", "")
            if url:
                try:
                    text, _ = await self._http_fetch_text(url)
                except Exception as exc:
                    return RPAResult(success=False, error=str(exc))
                self._http_pages[session_id] = text
            else:
                text = self._http_pages.get(session_id, "")
            # A CSS selector cannot be honoured over raw-HTML httpx text; return the
            # whole page text (the selector is recorded by callers for provenance).
            return RPAResult(success=True, output=text[:50_000])

        # rpa_screenshot: no browser → no image. Say so instead of claiming success.
        return RPAResult(
            success=False,
            error="NOT IMPLEMENTED: rpa_screenshot requires a real browser "
            "(httpx fallback cannot capture screenshots)",
        )

    async def _http_fetch_text(self, url: str) -> tuple[str, str]:
        """Fetch ``url`` and return ``(cleaned_text, title)`` — no ``raise_for_status``.

        Error responses still carry a body; we surface whatever text is present so
        the scrape degrades gracefully rather than dropping the page entirely.

        Redirects are followed manually and every hop is re-checked by the SSRF
        guard: with ``follow_redirects=True`` a public URL could 302 straight to
        ``169.254.169.254`` / loopback after only the first URL was validated.
        """
        import re

        import httpx

        from app.net.ssrf_guard import SSRFError, assert_public_url

        max_redirects = 5
        current = url
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            for _hop in range(max_redirects + 1):
                await asyncio.to_thread(
                    assert_public_url,
                    current,
                    allowed_domains=self._allowed_domains,
                    context="rpa_http_fallback",
                )
                resp = await client.get(current, headers={"User-Agent": "AgentVerse-RPA/1.0"})
                location = resp.headers.get("location", "") if resp.is_redirect else ""
                if not location:
                    break
                current = str(resp.url.join(location))
            else:
                raise SSRFError(f"rpa_http_fallback: too many redirects (>{max_redirects})")
        raw = resp.text
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        cleaned = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL
        )
        text = re.sub(r"<[^>]+>", " ", cleaned)
        text = re.sub(r"\s+", " ", text).strip()
        return text, title

    async def scrape_to_kb(
        self,
        *,
        url: str,
        knowledge_store: Any,
        embedder: Any,
        collection_id: str,
        tenant_ctx: Any,
        selectors: list[str] | None = None,
        source_type: str = "rpa",
        max_chars: int = 50_000,
    ) -> dict[str, Any]:
        """Scrape ``url`` via this executor and persist it to the knowledge base.

        The single reachable RPA→KB path usable outside the HTTP API (e.g. by the
        agent RPA tool): scrape → provenance-tagged chunks → cross-source dedup via
        ``exists_by_hash`` → the shared KB persistence helper. Returns a summary
        dict (``chunks_ingested``, ``deduplicated``, ``content_hash``).
        """
        from app.rpa.kb_emit import scrape_url_to_chunks

        scraped = await scrape_url_to_chunks(
            self,
            url=url,
            selectors=selectors,
            source_type=source_type,
            max_chars=max_chars,
        )
        if not scraped.content.strip():
            return {"chunks_ingested": 0, "deduplicated": False, "content_hash": ""}
        tenant_id = getattr(tenant_ctx, "tenant_id", "")
        if await knowledge_store.exists_by_hash(
            content_hash=scraped.content_hash,
            tenant_id=tenant_id,
            collection_id=collection_id,
        ):
            return {
                "chunks_ingested": 0,
                "deduplicated": True,
                "content_hash": scraped.content_hash,
            }
        from app.api.knowledge import _ingest_chunks_from_source

        ingested = await _ingest_chunks_from_source(
            knowledge_store, scraped.chunks, collection_id, tenant_ctx, embedder
        )
        return {
            "chunks_ingested": ingested,
            "deduplicated": False,
            "content_hash": scraped.content_hash,
        }

    async def _execute_simulation(self, *, tool_name: str, arguments: dict[str, Any]) -> RPAResult:
        """Honest failure when no real browser can run the command.

        This used to return ``success=True`` with a ``[simulated] ...`` string for
        every tool (and ``captcha_detected: false`` / "network idle" / "human help
        requested" for the P1.2 tools), so an agent without Playwright "clicked",
        "submitted forms" and "downloaded files" that never happened and the
        verifier marked the step complete. It now fails closed with an explicit
        NOT IMPLEMENTED error; callers that only need page text opt into the real
        httpx fallback via ``allow_http_fetch``.
        """
        del arguments
        if tool_name not in _KNOWN_RPA_TOOLS:
            return RPAResult(success=False, error=f"Unknown RPA tool: {tool_name}")
        if tool_name == "rpa_request_human_help":
            return RPAResult(
                success=False,
                error=(
                    "NOT IMPLEMENTED: rpa_request_human_help has no live takeover channel; "
                    "use an HITL approval step instead"
                ),
            )
        reason = (
            "Playwright is not installed"
            if not self._playwright_available
            else "no live browser page is available for this command"
        )
        return RPAResult(
            success=False,
            error=f"NOT IMPLEMENTED: {tool_name} requires a real browser ({reason})",
        )
