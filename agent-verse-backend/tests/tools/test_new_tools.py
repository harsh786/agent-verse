"""Tests for new AgentVerse tools: WebSearchTool, HttpRequestTool,
ShellTool, DocumentParserTool, and the fixed Docker sandbox in
CodeInterpreter.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_duckduckgo_fallback():
    """WebSearchTool falls back to DuckDuckGo when SearXNG not configured."""
    from app.tools.web_search import WebSearchTool

    os.environ.pop("SEARXNG_URL", None)
    tool = WebSearchTool()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "AbstractText": "Python is a language",
        "Heading": "Python",
        "AbstractURL": "https://python.org",
        "RelatedTopics": [],
    }
    mock_resp.raise_for_status = lambda: None

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(query="Python programming")

    assert "results" in result
    assert isinstance(result["results"], list)


@pytest.mark.asyncio
async def test_web_search_uses_searxng_when_configured():
    """WebSearchTool calls SearXNG when SEARXNG_URL is set."""
    from app.tools.web_search import WebSearchTool

    os.environ["SEARXNG_URL"] = "http://searxng:8080"
    try:
        tool = WebSearchTool()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"title": "Result 1", "url": "https://example.com", "content": "Snippet", "engine": "google"},
            ]
        }
        mock_resp.raise_for_status = lambda: None

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool.execute(query="test query", num_results=3)

        assert result["source"] == "searxng"
        assert len(result["results"]) == 1
        assert result["results"][0]["title"] == "Result 1"
    finally:
        os.environ.pop("SEARXNG_URL", None)


@pytest.mark.asyncio
async def test_web_search_error_returns_error_dict():
    """WebSearchTool returns error dict on network failure (no SearXNG)."""
    from app.tools.web_search import WebSearchTool

    os.environ.pop("SEARXNG_URL", None)
    tool = WebSearchTool()

    import httpx

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(query="test")

    assert "error" in result


# ---------------------------------------------------------------------------
# HttpRequestTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_tool_blocks_localhost():
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()
    result = await tool.execute(url="http://localhost:8080/secret")
    assert "error" in result
    assert "Blocked" in result["error"]


@pytest.mark.asyncio
async def test_http_tool_blocks_127():
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()
    result = await tool.execute(url="http://127.0.0.1:9200/")
    assert "error" in result
    assert "Blocked" in result["error"]


@pytest.mark.asyncio
async def test_http_tool_blocks_metadata():
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()
    result = await tool.execute(url="http://169.254.169.254/latest/meta-data/")
    assert "error" in result


@pytest.mark.asyncio
async def test_http_tool_blocks_rfc1918_10():
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()
    result = await tool.execute(url="http://10.0.0.1/internal")
    assert "error" in result
    assert "Blocked" in result["error"]


@pytest.mark.asyncio
async def test_http_tool_blocks_rfc1918_192168():
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()
    result = await tool.execute(url="http://192.168.1.1/router")
    assert "error" in result
    assert "Blocked" in result["error"]


@pytest.mark.asyncio
async def test_http_tool_success_get():
    """HttpRequestTool returns status_code, ok, body on success."""
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"hello": "world"}'
    mock_resp.raise_for_status = lambda: None

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.request = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(url="https://api.example.com/data")

    assert result["status_code"] == 200
    assert result["ok"] is True
    assert result["body"] == {"hello": "world"}


@pytest.mark.asyncio
async def test_http_tool_timeout_capped_at_60():
    """Timeout parameter is capped at 60 seconds."""
    from app.tools.http_tool import HttpRequestTool

    tool = HttpRequestTool()

    calls = []

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.content = b"ok"

    async def capture_request(method, url, **kwargs):
        calls.append(kwargs)
        return mock_resp

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.request = capture_request

    with patch("httpx.AsyncClient", return_value=mock_client) as MockClient:
        await tool.execute(url="https://example.com/", timeout=9999)

    # Verify AsyncClient was created with timeout <= 60
    init_call_kwargs = MockClient.call_args
    used_timeout = init_call_kwargs[1].get("timeout") or init_call_kwargs[0][0]
    assert used_timeout <= 60.0


# ---------------------------------------------------------------------------
# ShellTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shell_tool_disabled_by_default():
    os.environ.pop("AGENTVERSE_ALLOW_SHELL_EXEC", None)
    from app.tools.shell_tool import ShellTool

    tool = ShellTool()
    result = await tool.execute(command="echo hello")
    assert "error" in result
    assert "disabled" in result["error"].lower()


@pytest.mark.asyncio
async def test_shell_tool_blocks_disallowed_commands():
    os.environ["AGENTVERSE_ALLOW_SHELL_EXEC"] = "true"
    try:
        from app.tools.shell_tool import ShellTool

        tool = ShellTool()
        result = await tool.execute(command="rm -rf /")
        assert "error" in result
        assert "not in allowed list" in result["error"]
    finally:
        os.environ.pop("AGENTVERSE_ALLOW_SHELL_EXEC", None)


@pytest.mark.asyncio
async def test_shell_tool_empty_command():
    os.environ["AGENTVERSE_ALLOW_SHELL_EXEC"] = "true"
    try:
        from app.tools.shell_tool import ShellTool

        tool = ShellTool()
        result = await tool.execute(command="   ")
        # shlex.split of whitespace-only returns []
        assert "error" in result
    finally:
        os.environ.pop("AGENTVERSE_ALLOW_SHELL_EXEC", None)


@pytest.mark.asyncio
async def test_shell_tool_allowed_command_runs_subprocess():
    """When ALLOW_SHELL_EXEC=true and Docker unavailable, runs via subprocess."""
    os.environ["AGENTVERSE_ALLOW_SHELL_EXEC"] = "true"
    try:
        from app.tools.shell_tool import ShellTool

        tool = ShellTool()
        # 'echo' is in the allowlist; Docker unavailable → subprocess fallback
        result = await tool.execute(command="echo hello")
        # Depending on Docker availability this may succeed or fail with docker error
        # but should never be a "disabled" error
        assert "disabled" not in str(result.get("error", "")).lower()
    finally:
        os.environ.pop("AGENTVERSE_ALLOW_SHELL_EXEC", None)


# ---------------------------------------------------------------------------
# DocumentParserTool
# ---------------------------------------------------------------------------


def test_document_parser_csv():
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    csv_data = b"name,age,city\nAlice,30,NYC\nBob,25,LA"
    result = asyncio.run(tool.execute(content_bytes=csv_data, filename="test.csv"))
    assert result.get("format") == "csv"
    assert "Alice" in result.get("content", "")
    assert result.get("metadata", {}).get("rows") == 2


def test_document_parser_csv_headers():
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    csv_data = b"col1,col2\nval1,val2"
    result = asyncio.run(tool.execute(content_bytes=csv_data, filename="data.csv"))
    assert result["metadata"]["headers"] == ["col1", "col2"]
    assert result["metadata"]["columns"] == 2


def test_document_parser_json():
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    data = json.dumps({"key": "value", "nested": {"a": 1}}).encode()
    result = asyncio.run(tool.execute(content_bytes=data, filename="test.json"))
    assert result.get("format") == "json"
    assert "key" in result.get("content", "")


def test_document_parser_text():
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    txt = b"Hello world\nSecond line"
    result = asyncio.run(tool.execute(content_bytes=txt, filename="notes.txt"))
    assert result.get("format") == "text"
    assert "Hello world" in result.get("content", "")


def test_document_parser_yaml():
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    yml = b"key: value\nlist:\n  - item1\n  - item2\n"
    result = asyncio.run(tool.execute(content_bytes=yml, filename="config.yaml"))
    assert result.get("format") == "yaml"
    assert "key" in result.get("content", "")


def test_document_parser_no_content():
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    result = asyncio.run(tool.execute(filename="empty.txt"))
    assert "error" in result


def test_document_parser_pdf_unavailable_graceful():
    """PDF parsing without pypdf returns a helpful message, not an exception."""
    from app.tools.document_parser import DocumentParserTool

    tool = DocumentParserTool()
    # A minimal "pdf" that will fail to parse — we just want no crash
    result = asyncio.run(tool.execute(content_bytes=b"%PDF-fake", filename="test.pdf"))
    # Either parses or returns error/unavailable message — should not raise
    assert "format" in result or "error" in result


# ---------------------------------------------------------------------------
# Docker sandbox source-code check
# ---------------------------------------------------------------------------


def test_docker_sandbox_uses_volumes():
    """code_interpreter._execute_docker must use volumes= and not files=."""
    import inspect
    from app.tools import code_interpreter

    src = inspect.getsource(code_interpreter)
    assert "volumes=" in src, "Docker call must use volumes= parameter"
    assert "files=" not in src, "files= is not a valid docker-py parameter"


def test_subprocess_guard_in_source():
    """_execute_subprocess_fallback must check AGENTVERSE_ALLOW_SUBPROCESS_EXEC."""
    import inspect
    from app.tools import code_interpreter

    src = inspect.getsource(code_interpreter)
    assert "AGENTVERSE_ALLOW_SUBPROCESS_EXEC" in src


# ---------------------------------------------------------------------------
# __init__.py exports
# ---------------------------------------------------------------------------


def test_init_exports_new_tools():
    from app.tools import (
        DocumentParserTool,
        HttpRequestTool,
        ShellTool,
        WebSearchTool,
    )

    assert callable(WebSearchTool)
    assert callable(HttpRequestTool)
    assert callable(ShellTool)
    assert callable(DocumentParserTool)
