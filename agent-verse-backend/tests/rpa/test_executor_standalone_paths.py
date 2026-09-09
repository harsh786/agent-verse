"""Standalone Playwright path coverage for app/rpa/executor.py lines 396-652.

Injects a mocked playwright.async_api into sys.modules so that
_execute_playwright_standalone runs through every tool branch without
requiring a real browser.

Combines with the simulation-focused tests in test_executor_comprehensive.py
to raise executor.py coverage above 95%.
"""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rpa.executor import RPAExecutor, RPAResult

# ── Mock playwright helpers ─────────────────────────────────────────────────


def _make_locator(*, tag: str = "input", input_type: str = "") -> MagicMock:
    """Build a mock locator that the executor calls.

    `tag`/`input_type` control the return values of element.evaluate() to
    exercise the different field-type branches in rpa_submit_form. Uses
    cycle() so multiple fields in a single submit_form call each get the
    same (tag, input_type) pair without StopIteration.
    """
    from itertools import cycle

    locator = MagicMock()
    # evaluate is called twice per field (tagName then type). Cycle so any
    # number of fields stays covered.
    locator.evaluate = AsyncMock(side_effect=cycle([tag, input_type]))
    locator.check = AsyncMock()
    locator.uncheck = AsyncMock()
    locator.fill = AsyncMock()
    locator.click = AsyncMock()
    locator.first = locator  # so .first.click works the same way
    locator.wait_for = AsyncMock()
    return locator


def _make_download_cm(download: MagicMock) -> MagicMock:
    """Build a working `page.expect_download()` context manager.

    The executor does:
        async with page.expect_download() as download_info:
            await page.click(selector)
        download = await download_info.value
    So __aenter__ must return an object whose `.value` is *itself awaitable*
    (i.e. a coroutine that resolves to the download object).
    """
    expect_cm = MagicMock()
    expect_cm.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(value=AsyncMock(return_value=download)())
    )
    expect_cm.__aexit__ = AsyncMock(return_value=None)
    return expect_cm


def _make_page(
    *,
    title: str = "Test Page",
    inner_text_value: str = "Extracted text content",
    content_value: str = "<html><body>page content</body></html>",
    screenshot_bytes: bytes = b"\x89PNG\r\n\x1a\nSOMEDATA",
    select_values: list[str] | None = None,
    locator: MagicMock | None = None,
) -> MagicMock:
    """Build a fully mocked Playwright Page object."""
    loc = locator or _make_locator()
    page = MagicMock()
    page.set_default_timeout = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value=title)
    page.inner_text = AsyncMock(return_value=inner_text_value)
    page.content = AsyncMock(return_value=content_value)
    page.screenshot = AsyncMock(return_value=screenshot_bytes)
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.press = AsyncMock()
    page.evaluate = AsyncMock(side_effect=["input", "text"])  # for submit_form tagName/type
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.set_input_files = AsyncMock()
    page.select_option = AsyncMock(
        return_value=select_values if select_values is not None else ["opt1"]
    )
    page.get_by_text = MagicMock(return_value=loc)
    page.locator = MagicMock(return_value=loc)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    # expect_download context manager — built lazily per-test via _make_download_cm
    # but expose a sane default for tests that don't override it
    download = MagicMock()
    download.suggested_filename = "downloaded_file.pdf"
    download.save_as = AsyncMock()
    page.expect_download = MagicMock(return_value=_make_download_cm(download))
    return page


def _make_pw_stack(page: MagicMock) -> MagicMock:
    """Build the mock async_playwright() stack: pw.chromium.launch → browser → context → page."""
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    chromium = MagicMock()
    chromium.launch = AsyncMock(return_value=browser)
    pw = MagicMock()
    pw.chromium = chromium
    pw.__aenter__ = AsyncMock(return_value=pw)
    pw.__aexit__ = AsyncMock(return_value=None)
    mock_api = MagicMock()
    mock_api.async_playwright = MagicMock(return_value=pw)
    return mock_api


def _build_executor(*, artifact_store: object | None = None, headless: bool = True) -> tuple[RPAExecutor, MagicMock]:
    """Build executor that thinks playwright is available, plus the mock page it will use."""
    ex = RPAExecutor(artifact_store=artifact_store, headless=headless)
    ex._playwright_available = True
    ex._session_manager = None  # forces standalone path
    page = _make_page()
    return ex, page


def _inject(page: MagicMock) -> patch.dict:  # type: ignore[type-arg]
    """Inject mocked playwright package into sys.modules."""
    return patch.dict(
        sys.modules,
        {"playwright": _make_pw_stack(page), "playwright.async_api": _make_pw_stack(page)},
    )


# ── rpa_open_url ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_open_url_success() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_open_url", arguments={"url": "https://example.com"}, goal_id="g1"
        )
    assert result.success is True
    assert "example.com" in result.output
    assert "Test Page" in result.output


@pytest.mark.asyncio
async def test_pw_open_url_missing_url() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_open_url", arguments={}, goal_id="g1"
        )
    assert result.success is False
    assert "url" in result.error.lower()


# ── rpa_click ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_click_by_selector() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_click",
            arguments={"url": "https://example.com", "selector": "#btn"},
            goal_id="g1",
        )
    assert result.success is True
    assert "#btn" in result.output
    assert result.artifact_url is not None  # screenshot stored as base64


@pytest.mark.asyncio
async def test_pw_click_by_text_only() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_click", arguments={"text": "Submit"}, goal_id="g1"
        )
    assert result.success is True
    assert "Submit" in result.output


@pytest.mark.asyncio
async def test_pw_click_no_text_no_selector() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_click", arguments={}, goal_id="g1"
        )
    assert result.success is False
    assert "selector or text" in result.error


# ── rpa_type ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_type_success() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_type",
            arguments={"selector": "#email", "text": "user@example.com"},
            goal_id="g1",
        )
    assert result.success is True
    assert "#email" in result.output


@pytest.mark.asyncio
async def test_pw_type_missing_selector() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_type", arguments={"text": "hello"}, goal_id="g1"
        )
    assert result.success is False
    assert "selector required" in result.error


# ── rpa_extract_text ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_extract_text_success() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_extract_text", arguments={"selector": "body"}, goal_id="g1"
        )
    assert result.success is True
    assert result.output == "Extracted text content"


# ── rpa_screenshot ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_screenshot_no_artifact_store() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_screenshot", arguments={"name": "shot1"}, goal_id="g1"
        )
    assert result.success is True
    assert result.artifact_url is not None
    assert result.artifact_name == "shot1.png"


@pytest.mark.asyncio
async def test_pw_screenshot_with_artifact_store_success() -> None:
    artifact = MagicMock(uri="s3://bucket/shot1.png", name="shot1.png")
    store = MagicMock()
    store.write_bytes = AsyncMock(return_value=artifact)

    ex, page = _build_executor(artifact_store=store)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_screenshot", arguments={"name": "shot1"}, goal_id="g1"
        )
    assert result.success is True
    assert result.artifact_url == "s3://bucket/shot1.png"
    store.write_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_pw_screenshot_artifact_store_exception_uses_fallback() -> None:
    store = MagicMock()
    store.write_bytes = AsyncMock(side_effect=Exception("S3 unreachable"))
    ex, page = _build_executor(artifact_store=store)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_screenshot", arguments={"name": "shot1"}, goal_id="g1"
        )
    # Fallback keeps data URI
    assert result.success is True
    assert result.artifact_url is not None
    assert result.artifact_url.startswith("data:image/png;base64,")


# ── rpa_wait_for_text ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_wait_for_text_via_locator_success() -> None:
    ex, page = _build_executor()
    # default locator.wait_for is AsyncMock that returns None (no exception)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_wait_for_text", arguments={"text": "Loaded"}, goal_id="g1"
        )
    assert result.success is True
    assert "appeared on page" in result.output


@pytest.mark.asyncio
async def test_pw_wait_for_text_locator_fails_then_content_match() -> None:
    ex, page = _build_executor()
    # Override get_by_text so its locator raises in wait_for, and content contains the text
    locator = _make_locator()
    locator.wait_for = AsyncMock(side_effect=Exception("timeout 10000ms exceeded"))
    page.get_by_text = MagicMock(return_value=locator)
    page.content = AsyncMock(return_value="Page says: Loaded successfully")
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_wait_for_text", arguments={"text": "Loaded"}, goal_id="g1"
        )
    assert result.success is True
    assert "found in page content" in result.output


@pytest.mark.asyncio
async def test_pw_wait_for_text_missing() -> None:
    ex, page = _build_executor()
    locator = _make_locator()
    locator.wait_for = AsyncMock(side_effect=Exception("timeout"))
    page.get_by_text = MagicMock(return_value=locator)
    page.content = AsyncMock(return_value="unrelated content")
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_wait_for_text", arguments={"text": "MissingText"}, goal_id="g1"
        )
    assert result.success is False
    assert "did not appear" in result.error


@pytest.mark.asyncio
async def test_pw_wait_for_text_missing_text_arg() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_wait_for_text", arguments={}, goal_id="g1"
        )
    assert result.success is False
    assert "text argument required" in result.error


# ── rpa_select_option ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_select_option_by_value_success() -> None:
    ex, page = _build_executor()
    page.select_option = AsyncMock(return_value=["opt1"])
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_select_option",
            arguments={"selector": "#country", "value": "US"},
            goal_id="g1",
        )
    assert result.success is True
    assert "US" in result.output


@pytest.mark.asyncio
async def test_pw_select_option_falls_back_to_label() -> None:
    ex, page = _build_executor()
    # First call (by value) returns empty → executor retries via label → returns selected
    page.select_option = AsyncMock(side_effect=[[], ["United States"]])
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_select_option",
            arguments={"selector": "#country", "value": "United States"},
            goal_id="g1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_select_option_missing_selector() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_select_option", arguments={"value": "X"}, goal_id="g1"
        )
    assert result.success is False
    assert "selector argument required" in result.error


@pytest.mark.asyncio
async def test_pw_select_option_exception_returns_error() -> None:
    ex, page = _build_executor()
    page.select_option = AsyncMock(side_effect=Exception("element not found"))
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_select_option",
            arguments={"selector": "#country", "value": "US"},
            goal_id="g1",
        )
    assert result.success is False
    assert "Could not select" in result.error


# ── rpa_upload_file ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_upload_file_success(tmp_path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_text("some content")

    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_upload_file",
            arguments={"selector": "#file-input", "file_path": str(test_file)},
            goal_id="g1",
        )
    assert result.success is True
    assert "test.txt" in result.output


@pytest.mark.asyncio
async def test_pw_upload_file_missing_selector() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_upload_file", arguments={"file_path": "/tmp/x.txt"}, goal_id="g1"
        )
    assert result.success is False
    assert "selector argument required" in result.error


@pytest.mark.asyncio
async def test_pw_upload_file_missing_file_path() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_upload_file", arguments={"selector": "#file"}, goal_id="g1"
        )
    assert result.success is False
    assert "file_path argument required" in result.error


@pytest.mark.asyncio
async def test_pw_upload_file_not_found() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_upload_file",
            arguments={"selector": "#file", "file_path": "/nonexistent/file.txt"},
            goal_id="g1",
        )
    assert result.success is False
    assert "File not found" in result.error


@pytest.mark.asyncio
async def test_pw_upload_file_set_input_files_exception(tmp_path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    ex, page = _build_executor()
    page.set_input_files = AsyncMock(side_effect=Exception("input not interactable"))
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_upload_file",
            arguments={"selector": "#file", "file_path": str(test_file)},
            goal_id="g1",
        )
    assert result.success is False
    assert "input not interactable" in result.error


# ── rpa_download_file ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_download_file_missing_selector() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_download_file", arguments={}, goal_id="g1"
        )
    assert result.success is False
    assert "selector argument required" in result.error


@pytest.mark.asyncio
async def test_pw_download_file_success_no_artifact_store(tmp_path) -> None:
    """Download succeeds, file is saved to tmp_path (no artifact store)."""
    ex, page = _build_executor()
    # Write a real file first so getsize works
    real_file = tmp_path / "downloaded_file.pdf"
    real_file.write_bytes(b"PDFCONTENT")

    download = MagicMock()
    download.suggested_filename = "downloaded_file.pdf"
    download.save_as = AsyncMock()
    page.expect_download = MagicMock(return_value=_make_download_cm(download))

    # Patch tempfile.NamedTemporaryFile to return our real file path
    with _inject(page):
        with patch("tempfile.NamedTemporaryFile") as mock_tmp_factory:
            mock_file = MagicMock()
            mock_file.name = str(real_file)
            mock_file.__enter__ = MagicMock(return_value=mock_file)
            mock_file.__exit__ = MagicMock(return_value=None)
            mock_tmp_factory.return_value = mock_file
            result = await ex._execute_playwright_standalone(
                tool_name="rpa_download_file",
                arguments={"selector": "#dl-btn"},
                goal_id="g1",
            )
    assert result.success is True
    assert "downloaded_file.pdf" in result.output
    assert "bytes" in result.output


@pytest.mark.asyncio
async def test_pw_download_file_with_artifact_store(tmp_path) -> None:
    store = MagicMock()
    store.store_bytes = AsyncMock(return_value="s3://bucket/downloaded_file.pdf")
    store.write_bytes = AsyncMock()

    ex, page = _build_executor(artifact_store=store)
    real_file = tmp_path / "downloaded_file.pdf"
    real_file.write_bytes(b"PDFCONTENT")

    download = MagicMock()
    download.suggested_filename = "downloaded_file.pdf"
    download.save_as = AsyncMock()
    page.expect_download = MagicMock(return_value=_make_download_cm(download))

    with _inject(page):
        with patch("tempfile.NamedTemporaryFile") as mock_tmp_factory:
            mock_file = MagicMock()
            mock_file.name = str(real_file)
            mock_file.__enter__ = MagicMock(return_value=mock_file)
            mock_file.__exit__ = MagicMock(return_value=None)
            mock_tmp_factory.return_value = mock_file
            # Patch os.unlink so we can prove the finally block runs
            with patch("os.unlink") as mock_unlink:
                result = await ex._execute_playwright_standalone(
                    tool_name="rpa_download_file",
                    arguments={"selector": "#dl-btn"},
                    goal_id="g1",
                )
    assert result.success is True
    assert result.artifact_url == "s3://bucket/downloaded_file.pdf"
    store.store_bytes.assert_awaited_once()
    # Finally clause runs os.unlink — proves the cleanup path executed
    mock_unlink.assert_called_once_with(str(real_file))


@pytest.mark.asyncio
async def test_pw_download_file_artifact_store_failure_falls_back_to_tmp_path(
    tmp_path,
) -> None:
    """If artifact_store.store_bytes raises (lines 580-581 except/pass),
    the result still succeeds using the local tmp_path as the artifact URL."""
    store = MagicMock()
    store.store_bytes = AsyncMock(side_effect=Exception("S3 unreachable"))
    store.write_bytes = AsyncMock()

    ex, page = _build_executor(artifact_store=store)
    real_file = tmp_path / "downloaded_file.pdf"
    real_file.write_bytes(b"PDFCONTENT")

    download = MagicMock()
    download.suggested_filename = "downloaded_file.pdf"
    download.save_as = AsyncMock()
    page.expect_download = MagicMock(return_value=_make_download_cm(download))

    with _inject(page):
        with patch("tempfile.NamedTemporaryFile") as mock_tmp_factory:
            mock_file = MagicMock()
            mock_file.name = str(real_file)
            mock_file.__enter__ = MagicMock(return_value=mock_file)
            mock_file.__exit__ = MagicMock(return_value=None)
            mock_tmp_factory.return_value = mock_file
            with patch("os.unlink"):  # avoid real unlink so getsize below works
                result = await ex._execute_playwright_standalone(
                    tool_name="rpa_download_file",
                    arguments={"selector": "#dl-btn"},
                    goal_id="g1",
                )
    assert result.success is True
    # artifact_url fell back to tmp_path because store_bytes raised
    assert result.artifact_url == str(real_file)
    store.store_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_pw_download_file_exception_returns_error() -> None:
    ex, page = _build_executor()
    page.click = AsyncMock(side_effect=Exception("click timeout"))
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_download_file",
            arguments={"selector": "#dl-btn"},
            goal_id="g1",
        )
    assert result.success is False
    assert "click timeout" in result.error


# ── rpa_submit_form ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_submit_form_text_inputs_success() -> None:
    ex, page = _build_executor()
    # page.evaluate and element.evaluate must return tag/type consistently;
    # the executor calls `page.locator(sel)` and then `element.evaluate(...)` twice.
    locator = _make_locator(tag="input", input_type="text")
    page.locator = MagicMock(return_value=locator)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#name": "John", "#email": "john@test.com"}},
            goal_id="g1",
        )
    assert result.success is True
    assert "2 fields" in result.output


@pytest.mark.asyncio
async def test_pw_submit_form_select_field() -> None:
    ex, page = _build_executor()
    locator = _make_locator(tag="select", input_type="")
    page.locator = MagicMock(return_value=locator)
    page.select_option = AsyncMock(return_value=["opt1"])
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#country": "US"}},
            goal_id="g1",
        )
    assert result.success is True
    assert "1 fields" in result.output


@pytest.mark.asyncio
async def test_pw_submit_form_checkbox_true() -> None:
    ex, page = _build_executor()
    locator = _make_locator(tag="input", input_type="checkbox")
    page.locator = MagicMock(return_value=locator)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#agree": True}},
            goal_id="g1",
        )
    assert result.success is True
    locator.check.assert_awaited_once()
    locator.uncheck.assert_not_called()


@pytest.mark.asyncio
async def test_pw_submit_form_checkbox_false() -> None:
    ex, page = _build_executor()
    locator = _make_locator(tag="input", input_type="checkbox")
    page.locator = MagicMock(return_value=locator)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#agree": False}},
            goal_id="g1",
        )
    assert result.success is True
    locator.uncheck.assert_awaited_once()
    locator.check.assert_not_called()


@pytest.mark.asyncio
async def test_pw_submit_form_radio_true() -> None:
    ex, page = _build_executor()
    locator = _make_locator(tag="input", input_type="radio")
    page.locator = MagicMock(return_value=locator)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#choice": True}},
            goal_id="g1",
        )
    assert result.success is True
    locator.check.assert_awaited_once()


@pytest.mark.asyncio
async def test_pw_submit_form_empty_fields() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form", arguments={"field_values": {}}, goal_id="g1"
        )
    assert result.success is True
    assert "0 fields" in result.output


@pytest.mark.asyncio
async def test_pw_submit_form_submit_button_exception_falls_back_to_enter() -> None:
    """If click(submit_selector) raises, executor falls back to keyboard Enter."""
    ex, page = _build_executor()
    locator = _make_locator(tag="input", input_type="text")
    page.locator = MagicMock(return_value=locator)
    page.click = AsyncMock(side_effect=Exception("submit not clickable"))
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#name": "John"}},
            goal_id="g1",
        )
    assert result.success is True
    page.keyboard.press.assert_awaited_once()


@pytest.mark.asyncio
async def test_pw_submit_form_field_fill_exception() -> None:
    ex, page = _build_executor()
    locator = _make_locator(tag="input", input_type="text")
    locator.fill = AsyncMock(side_effect=Exception("field not visible"))
    page.locator = MagicMock(return_value=locator)
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#name": "John"}},
            goal_id="g1",
        )
    assert result.success is False
    assert "field not visible" in result.error


# ── Unknown tool falls back to simulation inside standalone ──────────────────


@pytest.mark.asyncio
async def test_pw_unknown_tool_falls_back_to_simulation() -> None:
    ex, page = _build_executor()
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_unknown_tool", arguments={}, goal_id="g1"
        )
    # Simulation handles all known tools; unknown returns "Unknown RPA tool"
    assert result.success is False
    assert "Unknown RPA tool" in result.error


# ── Import failure inside standalone falls back to simulation ────────────────


@pytest.mark.asyncio
async def test_pw_standalone_import_failure_falls_back_to_simulation() -> None:
    """If `from playwright.async_api import async_playwright` raises inside standalone,
    executor returns simulation result instead."""
    ex, page = _build_executor()
    # Make the injected playwright module NOT have async_playwright so import fails
    mock_api = MagicMock()
    del mock_api.async_playwright  # forces AttributeError on attribute access
    with patch.dict(
        sys.modules,
        {"playwright": mock_api, "playwright.async_api": mock_api},
    ):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_open_url", arguments={"url": "https://example.com"}, goal_id="g1"
        )
    # Falls through to simulation → success
    assert result.success is True
    assert "[simulated]" in result.output


# ── execute() routing — standalone path with playwright available ────────────


@pytest.mark.asyncio
async def test_execute_standalone_path_when_no_session_manager() -> None:
    """execute() routes to _execute_playwright_standalone when playwright is available
    but no session_manager is configured."""
    ex, page = _build_executor()
    with _inject(page):
        result = await ex.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
            tenant_id="t1",
            goal_id="g1",
        )
    assert result.success is True
    assert "example.com" in result.output
    # Ephemeral session close skipped because session_manager is None
    assert result.duration_ms > 0.0


# ── _execute_with_playwright with session manager ────────────────────────────


@pytest.mark.asyncio
async def test_execute_with_session_manager_text_input() -> None:
    """_execute_with_playwright uses the session's page for rpa_type."""
    page = _make_page()
    session = MagicMock()
    session.page = page
    session.current_url = None
    session.touch = MagicMock()

    session_mgr = MagicMock()
    session_mgr.get_or_create = AsyncMock(return_value=session)
    session_mgr.close = AsyncMock()

    ex = RPAExecutor(session_manager=session_mgr, headless=True)
    ex._playwright_available = True

    with _inject(page):
        result = await ex.execute(
            tool_name="rpa_type",
            arguments={"selector": "#email", "text": "user@test.com"},
            tenant_id="t1",
            goal_id="g1",
            session_id="persistent-session",
        )
    assert result.success is True
    assert "#email" in result.output
    # Persistent session NOT auto-closed
    session_mgr.close.assert_not_called()


# ── Outer exception handler hits (lines 629-630) ─────────────────────────────


@pytest.mark.asyncio
async def test_pw_extract_text_goto_exception_hits_outer_except() -> None:
    """If page.goto raises within a tool branch that has no inner try/except
    (rpa_extract_text success path), the outer standalone try/except catches
    it and returns a failure result (covers lines 629-630)."""
    ex, page = _build_executor()
    page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_CONNECTION_RESET"))
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_extract_text",
            arguments={"url": "https://example.com", "selector": "body"},
            goal_id="g1",
        )
    assert result.success is False
    assert "net::ERR_CONNECTION_RESET" in result.error


@pytest.mark.asyncio
async def test_pw_open_url_goto_exception_hits_outer_except() -> None:
    """rpa_open_url page.goto failure hits outer except (no inner try block on goto)."""
    ex, page = _build_executor()
    page.goto = AsyncMock(side_effect=RuntimeError("navigation timeout"))
    with _inject(page):
        result = await ex._execute_playwright_standalone(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
            goal_id="g1",
        )
    assert result.success is False
    assert "navigation timeout" in result.error


# ── Browser launch failure ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pw_standalone_browser_launch_failure_raises() -> None:
    """If chromium.launch raises inside async with, the exception propagates
    out of _execute_playwright_standalone (launch is not in the inner try block)."""
    ex, _page = _build_executor()
    # Build a pw stack whose chromium.launch raises
    chromium = MagicMock()
    chromium.launch = AsyncMock(side_effect=Exception("browser not installed"))
    pw = MagicMock()
    pw.chromium = chromium
    pw.__aenter__ = AsyncMock(return_value=pw)
    pw.__aexit__ = AsyncMock(return_value=None)
    mock_api = MagicMock()
    mock_api.async_playwright = MagicMock(return_value=pw)

    with patch.dict(sys.modules, {"playwright": mock_api, "playwright.async_api": mock_api}):
        with pytest.raises(Exception, match="browser not installed"):
            await ex._execute_playwright_standalone(
                tool_name="rpa_open_url", arguments={"url": "https://example.com"}, goal_id="g1"
            )
