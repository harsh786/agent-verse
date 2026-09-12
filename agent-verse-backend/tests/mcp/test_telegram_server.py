"""Behavioral tests for the Telegram connector's default-chat-id resolution and
parse-mode fallback (added so a goal can deliver to Telegram without discovering
a chat_id first, and so a Markdown glitch never eats the whole message)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.servers import telegram_server
from app.mcp.servers.telegram_server import (
    _effective_chat_id,
    _resolve_default_chat_id,
    call_tool,
)

_CREDS = {"api_key": "123:ABC", "default_chat_id": "555000"}


def _resp(status: int = 200, data: Any = None, text: str = "") -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {"ok": True, "result": {"message_id": 1, "chat": {"id": 555000}}}
    m.text = text or str(data or "")
    m.raise_for_status = MagicMock()
    return m


def _client(post: Any = None, get: MagicMock | None = None) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    mc.post = AsyncMock(side_effect=post) if isinstance(post, list) else AsyncMock(return_value=post or _resp())
    mc.get = AsyncMock(return_value=get or _resp())
    return mc


# --- pure resolver helpers ---------------------------------------------------


def test_default_chat_id_from_credentials():
    assert _resolve_default_chat_id({"default_chat_id": "42"}) == "42"
    assert _resolve_default_chat_id({"chat_id": 99}) == "99"
    assert _resolve_default_chat_id({}) == ""


def test_effective_chat_id_prefers_explicit_argument():
    assert _effective_chat_id({"chat_id": "explicit"}, _CREDS) == "explicit"
    # blank/whitespace argument falls back to the default
    assert _effective_chat_id({"chat_id": "  "}, _CREDS) == "555000"
    assert _effective_chat_id({}, _CREDS) == "555000"


# --- default chat id in call_tool -------------------------------------------


@pytest.mark.asyncio
async def test_send_message_uses_default_chat_id_when_omitted():
    mc = _client()
    with patch("httpx.AsyncClient", return_value=mc):
        res = await call_tool("telegram_send_message", {"text": "hi"}, credentials=_CREDS)
    assert res["ok"] is True
    # the outgoing payload carried the configured default chat id
    sent = mc.post.call_args.kwargs["json"]
    assert sent["chat_id"] == "555000"


@pytest.mark.asyncio
async def test_send_message_missing_chat_and_no_default_errors():
    mc = _client()
    with patch("httpx.AsyncClient", return_value=mc):
        res = await call_tool("telegram_send_message", {"text": "hi"}, credentials={"api_key": "123:ABC"})
    assert "error" in res and "default_chat_id" in res["error"]
    mc.post.assert_not_called()


# --- parse-mode fallback -----------------------------------------------------


@pytest.mark.asyncio
async def test_parse_mode_400_falls_back_to_plain_text():
    bad = _resp(status=400, text='{"ok":false,"description":"Bad Request: can\'t parse entities: ..."}')
    good = _resp()
    mc = _client(post=[bad, good])
    with patch("httpx.AsyncClient", return_value=mc):
        res = await call_tool(
            "telegram_send_message",
            {"text": "use telegram_send_message", "parse_mode": "Markdown"},
            credentials=_CREDS,
        )
    assert res["ok"] is True
    assert mc.post.await_count == 2
    # retry dropped parse_mode
    assert "parse_mode" not in mc.post.await_args_list[1].kwargs["json"]


@pytest.mark.asyncio
async def test_no_token_returns_error():
    res = await call_tool("telegram_send_message", {"text": "hi"}, credentials={})
    assert res["error"] == "TELEGRAM_BOT_TOKEN not configured"


def test_send_tools_no_longer_require_chat_id():
    defs = {d["name"]: d for d in telegram_server.TOOL_DEFINITIONS}
    for name in ("telegram_send_message", "telegram_send_document", "telegram_send_photo"):
        assert "chat_id" not in defs[name]["parameters"]["required"]
