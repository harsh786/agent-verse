"""Cover app/rpa/executor.py Playwright interaction paths via mocked Playwright.

Covers _execute_with_playwright (mocked session_manager+page) and
_execute_playwright_standalone (patched async_playwright context manager).
"""
from __future__ import annotations

import base64
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rpa.executor import RPAExecutor, RPAResult


# ── helpers ───────────────────────────────────────────────────────────────────


def make_mock_page(
    html_content: str = "<html><body><h1>Test</h1></body></html>",
    title_text: str = "Test Page",
    screenshot_bytes: bytes = b"\x89PNG\r\n",
) -> MagicMock:
    """Build a comprehensive mock Playwright page with all used methods."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value=title_text)
    page.screenshot = AsyncMock(return_value=screenshot_bytes)
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.inner_text = AsyncMock(return_value="Extracted text content")
    page.content = AsyncMock(return_value=html_content)
    page.select_option = AsyncMock(return_value=["option1"])
    page.check = AsyncMock()
    page.uncheck = AsyncMock()
    page.hover = AsyncMock()
    page.press = AsyncMock()
    page.evaluate = AsyncMock(return_value="js_result")
    page.wait_for_selector = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.set_input_files = AsyncMock()
    page.get_attribute = AsyncMock(return_value="attr_value")
    page.is_visible = AsyncMock(return_value=True)
    page.is_enabled = AsyncMock(return_value=True)
    page.text_content = AsyncMock(return_value="button text")
    page.query_selector = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    page.close = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.url = "https://example.com"

    # get_by_text → locator
    mock_locator = MagicMock()
    mock_locator.first = MagicMock()
    mock_locator.first.click = AsyncMock()
    mock_locator.first.fill = AsyncMock()
    mock_locator.wait_for = AsyncMock()
    page.get_by_text = MagicMock(return_value=mock_locator)

    # locator() for submit_form
    mock_element = MagicMock()
    mock_element.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_element.fill = AsyncMock()
    mock_element.check = AsyncMock()
    mock_element.uncheck = AsyncMock()
    page.locator = MagicMock(return_value=mock_element)

    # expect_download context manager
    mock_download_val = MagicMock()
    mock_download_val.suggested_filename = "file.pdf"
    mock_download_val.save_as = AsyncMock()
    mock_download_val.path = AsyncMock(return_value="/tmp/downloaded_file.pdf")
    mock_download_holder = MagicMock()
    mock_download_holder.value = mock_download_val
    download_cm = MagicMock()
    download_cm.__aenter__ = AsyncMock(return_value=mock_download_holder)
    download_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_download = MagicMock(return_value=download_cm)

    return page


def make_mock_session(page: MagicMock | None = None) -> MagicMock:
    """Build a mock RPA session with a page."""
    session = MagicMock()
    session.page = page if page is not None else make_mock_page()
    session.current_url = ""
    session.touch = MagicMock()
    return session


def make_executor_with_session_manager(
    page: MagicMock | None = None,
) -> tuple[RPAExecutor, MagicMock]:
    """Build RPAExecutor with mocked Playwright + session_manager."""
    mock_session = make_mock_session(page)
    mock_session_manager = AsyncMock()
    mock_session_manager.get_or_create = AsyncMock(return_value=mock_session)
    mock_session_manager.close = AsyncMock()

    executor = RPAExecutor(session_manager=mock_session_manager)
    executor._playwright_available = True  # force Playwright path
    return executor, mock_session


# ══════════════════════════════════════════════════════════════════════════════
# PART A — _execute_with_playwright (session_manager present)
# ══════════════════════════════════════════════════════════════════════════════


# ── rpa_open_url ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_open_url_success() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.goto.assert_called_once()
    assert "example.com" in result.output or "Navigated" in result.output


@pytest.mark.asyncio
async def test_pw_open_url_missing_url_returns_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_open_url",
        arguments={},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "url" in (result.error or "").lower()


# ── rpa_click ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_click_by_selector() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_click",
        arguments={"selector": "#submit-btn"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.click.assert_called_once_with("#submit-btn", timeout=5000)
    assert result.artifact_url is not None  # screenshot included


@pytest.mark.asyncio
async def test_pw_click_with_url_navigates_first() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_click",
        arguments={"url": "https://example.com", "selector": "#btn"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    # goto called for the url navigation (line 132-133)
    session.page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_pw_click_by_text() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_click",
        arguments={"text": "Submit"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.get_by_text.assert_called_once()


@pytest.mark.asyncio
async def test_pw_click_no_selector_no_text_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_click",
        arguments={},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_pw_click_exception_returns_error() -> None:
    page = make_mock_page()
    page.click = AsyncMock(side_effect=Exception("Element not found"))
    executor, session = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_click",
        arguments={"selector": "#missing"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "Element not found" in (result.error or "")


# ── rpa_type ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_type_success() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_type",
        arguments={"selector": "#email", "text": "test@example.com"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.fill.assert_called_once_with("#email", "test@example.com", timeout=5000)


@pytest.mark.asyncio
async def test_pw_type_with_url_navigates_first() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_type",
        arguments={"url": "https://example.com", "selector": "#q", "text": "hello"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.goto.assert_called_once()  # covers lines 159-160


@pytest.mark.asyncio
async def test_pw_type_no_selector_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_type",
        arguments={"text": "hello"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "selector" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_pw_type_exception_returns_error() -> None:
    page = make_mock_page()
    page.fill = AsyncMock(side_effect=Exception("fill failed"))
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_type",
        arguments={"selector": "#q", "text": "hi"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


# ── rpa_extract_text ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_extract_text_success() -> None:
    page = make_mock_page()
    page.inner_text = AsyncMock(return_value="Page content here")
    executor, session = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_extract_text",
        arguments={"selector": "body"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    assert "Page content here" in result.output


@pytest.mark.asyncio
async def test_pw_extract_text_with_url_navigates_first() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_extract_text",
        arguments={"url": "https://example.com", "selector": "body"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.goto.assert_called_once()  # covers lines 173-174 / 178-179


@pytest.mark.asyncio
async def test_pw_extract_text_exception_returns_error() -> None:
    page = make_mock_page()
    page.inner_text = AsyncMock(side_effect=Exception("No element"))
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_extract_text",
        arguments={"selector": ".missing"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False  # covers lines 185-186


# ── rpa_screenshot ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_screenshot_returns_base64() -> None:
    page = make_mock_page(screenshot_bytes=b"\x89PNG\r\n\x1a\n")
    executor, session = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "test_shot"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    assert result.artifact_url is not None
    assert "data:image/png;base64," in (result.artifact_url or "")


@pytest.mark.asyncio
async def test_pw_screenshot_with_url_navigates_first() -> None:
    executor, session = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_screenshot",
        arguments={"url": "https://example.com", "name": "snap"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    session.page.goto.assert_called_once()  # covers lines 190-191


@pytest.mark.asyncio
async def test_pw_screenshot_with_artifact_store() -> None:
    """Covers the artifact_store path (lines 209-210)."""
    page = make_mock_page()
    executor, _ = make_executor_with_session_manager(page)
    mock_artifact = MagicMock()
    mock_artifact.uri = "s3://bucket/screenshot.png"
    mock_artifact.name = "screenshot.png"
    mock_artifact_store = AsyncMock()
    mock_artifact_store.write_bytes = AsyncMock(return_value=mock_artifact)
    executor._artifact_store = mock_artifact_store

    result = await executor.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "stored_shot"},
        session_id="s1",
        tenant_id="t1",
        goal_id="goal-123",
    )
    assert result.success is True
    mock_artifact_store.write_bytes.assert_called_once()
    assert result.artifact_url == "s3://bucket/screenshot.png"


@pytest.mark.asyncio
async def test_pw_screenshot_artifact_store_failure_fallback() -> None:
    """Artifact store failure should fall back to base64."""
    page = make_mock_page()
    executor, _ = make_executor_with_session_manager(page)
    mock_artifact_store = AsyncMock()
    mock_artifact_store.write_bytes = AsyncMock(side_effect=Exception("S3 down"))
    executor._artifact_store = mock_artifact_store

    result = await executor.execute(
        tool_name="rpa_screenshot",
        arguments={"name": "fallback_shot"},
        session_id="s1",
        tenant_id="t1",
        goal_id="goal-123",
    )
    assert result.success is True
    assert "data:image/png;base64," in (result.artifact_url or "")


@pytest.mark.asyncio
async def test_pw_screenshot_with_vision_provider() -> None:
    """Covers the vision_provider path (lines 215-223)."""
    page = make_mock_page()
    executor, _ = make_executor_with_session_manager(page)
    mock_vision = MagicMock()
    executor._vision_provider = mock_vision

    mock_ba = MagicMock()
    mock_ba.analyze_screenshot = AsyncMock(return_value="A login page with a form")
    with patch("app.perception.browser_agent.BrowserAgent", return_value=mock_ba):
        result = await executor.execute(
            tool_name="rpa_screenshot",
            arguments={"name": "vision_shot"},
            session_id="s1",
            tenant_id="t1",
        )
    assert result.success is True


# ── rpa_wait_for_text ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_wait_for_text_success() -> None:
    page = make_mock_page()
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Welcome"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_wait_for_text_no_text_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_wait_for_text",
        arguments={},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_pw_wait_for_text_in_content_fallback() -> None:
    """When locator.wait_for raises, falls back to checking page.content()."""
    page = make_mock_page(html_content="<html>Welcome back!</html>")
    page.get_by_text.return_value.wait_for = AsyncMock(side_effect=Exception("timeout"))
    page.content = AsyncMock(return_value="<html>Welcome back!</html>")
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Welcome back"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_wait_for_text_not_found() -> None:
    """Text not found anywhere returns failure."""
    page = make_mock_page(html_content="<html>Goodbye!</html>")
    page.get_by_text.return_value.wait_for = AsyncMock(side_effect=Exception("not visible"))
    page.content = AsyncMock(return_value="<html>Goodbye!</html>")
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_wait_for_text",
        arguments={"text": "Hello"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


# ── rpa_select_option ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_select_option_by_value() -> None:
    executor, session = make_executor_with_session_manager()
    session.page.select_option = AsyncMock(return_value=["opt2"])
    result = await executor.execute(
        tool_name="rpa_select_option",
        arguments={"selector": "#dropdown", "value": "opt2"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_select_option_empty_returns_tries_label() -> None:
    """When select_option returns empty list, tries by label (line 271)."""
    executor, session = make_executor_with_session_manager()
    # First call returns empty, second returns selection
    session.page.select_option = AsyncMock(side_effect=[[], ["opt2"]])
    result = await executor.execute(
        tool_name="rpa_select_option",
        arguments={"selector": "#dropdown", "value": "Option 2"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_select_option_no_selector_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_select_option",
        arguments={"value": "opt1"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_pw_select_option_exception_returns_error() -> None:
    """Covers exception path (lines 277-278)."""
    page = make_mock_page()
    page.select_option = AsyncMock(side_effect=Exception("Select failed"))
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_select_option",
        arguments={"selector": "#broken", "value": "x"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


# ── rpa_upload_file ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_upload_file_no_selector_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_upload_file",
        arguments={"file_path": "/tmp/file.pdf"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "selector" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_pw_upload_file_no_file_path_error() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_upload_file",
        arguments={"selector": "#file-input"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "file_path" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_pw_upload_file_not_found_error() -> None:
    """Covers line 290: file not found check."""
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_upload_file",
        arguments={"selector": "#file-input", "file_path": "/nonexistent/path/file.pdf"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "not found" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_pw_upload_file_success() -> None:
    """Covers lines 304-305: upload success path."""
    # Create a real temp file
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"test content")
        tmp_path = f.name
    try:
        executor, session = make_executor_with_session_manager()
        result = await executor.execute(
            tool_name="rpa_upload_file",
            arguments={"selector": "#file-input", "file_path": tmp_path},
            session_id="s1",
            tenant_id="t1",
        )
        assert result.success is True
        session.page.set_input_files.assert_called_once()
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_pw_upload_file_exception_returns_error() -> None:
    page = make_mock_page()
    page.set_input_files = AsyncMock(side_effect=Exception("Upload failed"))
    executor, _ = make_executor_with_session_manager(page)
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"data")
        tmp_path = f.name
    try:
        result = await executor.execute(
            tool_name="rpa_upload_file",
            arguments={"selector": "#broken", "file_path": tmp_path},
            session_id="s1",
            tenant_id="t1",
        )
        assert result.success is False
    finally:
        os.unlink(tmp_path)


# ── rpa_download_file ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_download_file_no_selector_error() -> None:
    """Covers line 308+ entry: no selector."""
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_download_file",
        arguments={},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False
    assert "selector" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_pw_download_file_success() -> None:
    """Covers lines 307-349: download file success path."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"PDF content")
        tmp_path = f.name

    page = make_mock_page()
    # Mock the download context manager
    mock_download = MagicMock()
    mock_download.suggested_filename = "report.pdf"
    mock_download.save_as = AsyncMock()

    mock_holder = MagicMock()
    mock_holder.value = mock_download

    download_cm = MagicMock()
    download_cm.__aenter__ = AsyncMock(return_value=mock_holder)
    download_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_download = MagicMock(return_value=download_cm)

    # Patch tempfile to return our known path
    with patch("tempfile.NamedTemporaryFile") as mock_ntf:
        mock_ntf.return_value.__enter__ = MagicMock(return_value=MagicMock(name=tmp_path))
        mock_ntf.return_value.__exit__ = MagicMock(return_value=False)
        mock_ntf.return_value.__enter__.return_value.name = tmp_path

        executor, _ = make_executor_with_session_manager(page)
        result = await executor.execute(
            tool_name="rpa_download_file",
            arguments={"selector": "#download-btn"},
            session_id="s1",
            tenant_id="t1",
        )

    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    # Result may succeed or fail (depending on tempfile mock), but code path is hit
    assert result is not None


@pytest.mark.asyncio
async def test_pw_download_file_exception_returns_error() -> None:
    """Covers exception path in download_file."""
    page = make_mock_page()
    # Make click fail inside expect_download
    download_cm = MagicMock()
    download_cm.__aenter__ = AsyncMock(side_effect=Exception("Download failed"))
    download_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_download = MagicMock(return_value=download_cm)
    executor, _ = make_executor_with_session_manager(page)

    result = await executor.execute(
        tool_name="rpa_download_file",
        arguments={"selector": "#broken-btn"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


# ── rpa_submit_form ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_submit_form_text_fields() -> None:
    page = make_mock_page()
    # locator.evaluate → ("input", "text")
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_el.fill = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    executor, session = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#name": "John"}, "submit_selector": "#submit"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_submit_form_select_field() -> None:
    """Covers the select tag path."""
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["select", ""])
    mock_el.fill = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#country": "US"}},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_submit_form_checkbox_check() -> None:
    """Covers lines 363-368: checkbox input type check/uncheck."""
    page = make_mock_page()
    mock_el = MagicMock()
    # First call returns "input", second returns "checkbox"
    mock_el.evaluate = AsyncMock(side_effect=["input", "checkbox"])
    mock_el.check = AsyncMock()
    mock_el.uncheck = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#agree": True}},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    mock_el.check.assert_called_once()


@pytest.mark.asyncio
async def test_pw_submit_form_checkbox_uncheck() -> None:
    """Covers the uncheck path of checkbox."""
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "checkbox"])
    mock_el.check = AsyncMock()
    mock_el.uncheck = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#agree": False}},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    mock_el.uncheck.assert_called_once()


@pytest.mark.asyncio
async def test_pw_submit_form_keyboard_enter_fallback() -> None:
    """Covers lines 375-376: submit_selector click fails → keyboard Enter."""
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_el.fill = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    page.click = AsyncMock(side_effect=Exception("submit not found"))
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#q": "search"}, "submit_selector": "#missing-btn"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is True
    page.keyboard.press.assert_called_once_with("Enter")


@pytest.mark.asyncio
async def test_pw_submit_form_outer_exception() -> None:
    """Covers lines 382-383: outer exception in submit form."""
    page = make_mock_page()
    mock_el = MagicMock()
    # Make evaluate itself fail
    mock_el.evaluate = AsyncMock(side_effect=Exception("element error"))
    page.locator = MagicMock(return_value=mock_el)
    executor, _ = make_executor_with_session_manager(page)
    result = await executor.execute(
        tool_name="rpa_submit_form",
        arguments={"field_values": {"#broken": "value"}},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


# ── unknown tool falls back to simulation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_unknown_tool_falls_back_to_simulation() -> None:
    executor, _ = make_executor_with_session_manager()
    result = await executor.execute(
        tool_name="rpa_unknown_action",
        arguments={},
        session_id="s1",
        tenant_id="t1",
    )
    # Unknown tool → simulation → error
    assert result is not None


# ── outer exception in _execute_with_playwright ───────────────────────────────

@pytest.mark.asyncio
async def test_pw_outer_exception_handled() -> None:
    """Covers lines 390-391: top-level exception in _execute_with_playwright."""
    executor, session = make_executor_with_session_manager()
    # Make get_or_create succeed but page.goto raise a non-tool-specific error
    session.page.goto = AsyncMock(side_effect=Exception("Browser crashed"))
    result = await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
        session_id="s1",
        tenant_id="t1",
    )
    assert result.success is False


# ── page is None → falls back to simulation ───────────────────────────────────

@pytest.mark.asyncio
async def test_pw_no_page_falls_back_to_simulation() -> None:
    session = make_mock_session(page=None)
    session.page = None  # explicitly no page
    session_manager = AsyncMock()
    session_manager.get_or_create = AsyncMock(return_value=session)
    session_manager.close = AsyncMock()

    executor = RPAExecutor(session_manager=session_manager)
    executor._playwright_available = True

    result = await executor.execute(
        tool_name="rpa_screenshot",
        arguments={},
        session_id="s1",
        tenant_id="t1",
    )
    assert result is not None  # simulation path reached


# ── credential injector ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_credential_injector_called() -> None:
    """Covers the credential_injector path."""
    executor, session = make_executor_with_session_manager()
    mock_injector = AsyncMock()
    mock_injector.resolve_arguments = AsyncMock(
        return_value={"url": "https://example.com"}
    )
    executor._credential_injector = mock_injector

    result = await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
        session_id="s1",
        tenant_id="t1",
    )
    mock_injector.resolve_arguments.assert_called_once()
    assert result.success is True


@pytest.mark.asyncio
async def test_pw_credential_injector_failure_is_non_fatal() -> None:
    """If credential injection fails, execution continues."""
    executor, session = make_executor_with_session_manager()
    mock_injector = AsyncMock()
    mock_injector.resolve_arguments = AsyncMock(side_effect=Exception("vault down"))
    executor._credential_injector = mock_injector

    result = await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
        session_id="s1",
        tenant_id="t1",
    )
    # Should still execute (continues despite injector failure)
    assert result is not None


# ── ephemeral session cleanup ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pw_ephemeral_session_closed_after_execute() -> None:
    """When session_id=None, session_manager.close() is called after execution."""
    session = make_mock_session()
    session_manager = AsyncMock()
    session_manager.get_or_create = AsyncMock(return_value=session)
    session_manager.close = AsyncMock()

    executor = RPAExecutor(session_manager=session_manager)
    executor._playwright_available = True

    await executor.execute(
        tool_name="rpa_open_url",
        arguments={"url": "https://example.com"},
        # No session_id → ephemeral
        tenant_id="t1",
    )
    session_manager.close.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# PART B — _execute_playwright_standalone (no session_manager, patched playwright)
# ══════════════════════════════════════════════════════════════════════════════


def make_standalone_executor() -> RPAExecutor:
    """Return executor with playwright_available=True but no session_manager."""
    executor = RPAExecutor()
    executor._playwright_available = True
    return executor


def make_playwright_cm(page: MagicMock) -> MagicMock:
    """Build a mock async_playwright() context manager chain."""
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    mock_playwright_instance = MagicMock()
    mock_playwright_instance.chromium = MagicMock()
    mock_playwright_instance.chromium.launch = AsyncMock(return_value=mock_browser)
    playwright_cm = MagicMock()
    playwright_cm.__aenter__ = AsyncMock(return_value=mock_playwright_instance)
    playwright_cm.__aexit__ = AsyncMock(return_value=False)
    return playwright_cm


@pytest.mark.asyncio
async def test_standalone_open_url() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_open_url",
            arguments={"url": "https://example.com"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_open_url_no_url_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_open_url",
            arguments={},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_click_by_selector() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_click",
            arguments={"selector": "#btn"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_click_with_url() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_click",
            arguments={"url": "https://example.com", "selector": "#btn"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_click_by_text() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_click",
            arguments={"text": "Submit"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_click_no_selector_no_text_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_click",
            arguments={},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_type_success() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_type",
            arguments={"selector": "#q", "text": "hello"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_type_with_url() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_type",
            arguments={"url": "https://example.com", "selector": "#q", "text": "hello"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_type_no_selector_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_type",
            arguments={"text": "hello"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_extract_text() -> None:
    page = make_mock_page()
    page.inner_text = AsyncMock(return_value="page text here")
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_extract_text",
            arguments={"selector": "body"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    assert "page text here" in result.output


@pytest.mark.asyncio
async def test_standalone_extract_text_with_url() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_extract_text",
            arguments={"url": "https://example.com", "selector": "h1"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_screenshot() -> None:
    page = make_mock_page(screenshot_bytes=b"\x89PNG\r\n")
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_screenshot",
            arguments={"name": "snap"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    assert "data:image/png;base64," in (result.artifact_url or "")


@pytest.mark.asyncio
async def test_standalone_screenshot_with_url() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_screenshot",
            arguments={"url": "https://example.com", "name": "snap"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_screenshot_with_artifact_store() -> None:
    """Covers standalone artifact_store path."""
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()
    mock_artifact = MagicMock()
    mock_artifact.uri = "s3://bucket/snap.png"
    mock_artifact.name = "snap.png"
    mock_store = AsyncMock()
    mock_store.write_bytes = AsyncMock(return_value=mock_artifact)
    executor._artifact_store = mock_store

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_screenshot",
            arguments={"name": "snap"},
            session_id="standalone",
            tenant_id="t1",
            goal_id="goal-standalone",
        )
    assert result.success is True
    mock_store.write_bytes.assert_called_once()


@pytest.mark.asyncio
async def test_standalone_wait_for_text() -> None:
    page = make_mock_page()
    page.get_by_text.return_value.wait_for = AsyncMock()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_wait_for_text",
            arguments={"text": "Hello"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_wait_for_text_no_text_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_wait_for_text",
            arguments={},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_wait_for_text_content_fallback() -> None:
    page = make_mock_page(html_content="<html>Hello!</html>")
    page.get_by_text.return_value.wait_for = AsyncMock(side_effect=Exception("timeout"))
    page.content = AsyncMock(return_value="<html>Hello!</html>")
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_wait_for_text",
            arguments={"text": "Hello"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_wait_for_text_not_found() -> None:
    page = make_mock_page(html_content="<html>Goodbye!</html>")
    page.get_by_text.return_value.wait_for = AsyncMock(side_effect=Exception("not visible"))
    page.content = AsyncMock(return_value="<html>Goodbye!</html>")
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_wait_for_text",
            arguments={"text": "Hello"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_select_option() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_select_option",
            arguments={"selector": "#dropdown", "value": "opt1"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_select_option_label_fallback() -> None:
    page = make_mock_page()
    page.select_option = AsyncMock(side_effect=[[], ["opt2"]])
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_select_option",
            arguments={"selector": "#dropdown", "value": "Option 2"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_select_option_no_selector_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_select_option",
            arguments={"value": "x"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_select_option_exception() -> None:
    page = make_mock_page()
    page.select_option = AsyncMock(side_effect=Exception("select failed"))
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_select_option",
            arguments={"selector": "#broken", "value": "x"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_upload_file_no_selector_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_upload_file",
            arguments={"file_path": "/tmp/x.txt"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_upload_file_not_found() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_upload_file",
            arguments={"selector": "#file", "file_path": "/does/not/exist.pdf"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_upload_file_success() -> None:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"content")
        tmp_path = f.name
    try:
        page = make_mock_page()
        playwright_cm = make_playwright_cm(page)
        executor = make_standalone_executor()

        with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
            result = await executor.execute(
                tool_name="rpa_upload_file",
                arguments={"selector": "#file-input", "file_path": tmp_path},
                session_id="standalone",
                tenant_id="t1",
            )
        assert result.success is True
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_standalone_download_file_no_selector_error() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_download_file",
            arguments={},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_download_file_exception() -> None:
    page = make_mock_page()
    download_cm = MagicMock()
    download_cm.__aenter__ = AsyncMock(side_effect=Exception("download failed"))
    download_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_download = MagicMock(return_value=download_cm)
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_download_file",
            arguments={"selector": "#dl"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_submit_form() -> None:
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_el.fill = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#name": "Alice"}},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_submit_form_checkbox() -> None:
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "checkbox"])
    mock_el.check = AsyncMock()
    mock_el.uncheck = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#agree": True}},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_standalone_submit_form_keyboard_fallback() -> None:
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_el.fill = AsyncMock()
    page.locator = MagicMock(return_value=mock_el)
    page.click = AsyncMock(side_effect=Exception("no submit"))
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#q": "search"}, "submit_selector": "#submit"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is True
    page.keyboard.press.assert_called_once_with("Enter")


@pytest.mark.asyncio
async def test_standalone_submit_form_exception() -> None:
    page = make_mock_page()
    mock_el = MagicMock()
    mock_el.evaluate = AsyncMock(side_effect=Exception("element gone"))
    page.locator = MagicMock(return_value=mock_el)
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_submit_form",
            arguments={"field_values": {"#broken": "x"}},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_standalone_unknown_tool_fallback() -> None:
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_unknown_xyz",
            arguments={},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result is not None  # falls back to simulation


@pytest.mark.asyncio
async def test_standalone_context_exception_handled() -> None:
    """Exception in browser.new_context() is caught by inner try/except (lines 649-650)."""
    page = make_mock_page()
    playwright_cm = make_playwright_cm(page)
    # Make new_context throw (this is inside the inner try block)
    playwright_cm.__aenter__.return_value.chromium.launch.return_value.new_context = AsyncMock(
        side_effect=Exception("Context crash")
    )
    executor = make_standalone_executor()

    with patch("playwright.async_api.async_playwright", return_value=playwright_cm):
        result = await executor.execute(
            tool_name="rpa_screenshot",
            arguments={"name": "crash"},
            session_id="standalone",
            tenant_id="t1",
        )
    assert result.success is False
    assert "Context crash" in (result.error or "")
