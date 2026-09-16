"""Tests for DiscordConnector — channel message ingestion via bot token."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.discord_connector import DiscordConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-discord",
        tenant_id="t1",
        name="Test Discord",
        family="communication",
        source_type="discord",
        connection_config=conn_config or {},
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


def _fake_client(get_impl):
    client = AsyncMock()
    client.get = get_impl
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestValidateConnection:
    async def test_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"username": "AgentBot", "id": "999"})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"bot_token": "tok"})
        with patch("httpx.AsyncClient", return_value=client):
            result = await DiscordConnector().validate_connection(config)
        assert result.ok is True
        assert result.metadata["bot"] == "AgentBot"

    async def test_failure(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=Exception("403 forbidden"))
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"bot_token": "bad"})
        with patch("httpx.AsyncClient", return_value=client):
            result = await DiscordConnector().validate_connection(config)
        assert result.ok is False
        assert "forbidden" in result.error


class TestGetDelta:
    async def test_yields_messages_oldest_first(self):
        # API returns newest-first; connector reverses for oldest-first processing.
        messages = [
            {
                "id": "200",
                "author": {"username": "bob"},
                "content": "second message",
                "timestamp": "2026-01-02T00:00:00Z",
            },
            {
                "id": "100",
                "author": {"username": "alice"},
                "content": "first message",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ]
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value=messages)
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"bot_token": "tok", "channel_ids": ["chan1"], "batch_size": 100})

        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(DiscordConnector().get_delta(config, None))

        assert len(docs) == 2
        doc0, cursor0 = docs[0]
        assert "alice" in doc0.content.decode()
        assert cursor0 == "100"
        doc1, cursor1 = docs[1]
        assert "bob" in doc1.content.decode()
        assert cursor1 == "200"

    async def test_skips_empty_content_messages(self):
        messages = [{"id": "1", "author": {"username": "bob"}, "content": "   ", "timestamp": "t"}]
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value=messages)
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"bot_token": "tok", "channel_ids": ["chan1"]})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(DiscordConnector().get_delta(config, None))
        assert docs == []

    async def test_http_failure_moves_to_next_channel(self):
        fail_resp = MagicMock(is_success=False, status_code=403)
        ok_resp = MagicMock()
        ok_resp.is_success = True
        ok_resp.json = MagicMock(
            return_value=[{"id": "5", "author": {"username": "x"}, "content": "hi", "timestamp": "t"}]
        )
        get_mock = AsyncMock(side_effect=[fail_resp, ok_resp])
        client = _fake_client(get_mock)
        config = _make_config({"bot_token": "tok", "channel_ids": ["bad_chan", "good_chan"]})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(DiscordConnector().get_delta(config, None))
        assert len(docs) == 1

    async def test_pagination_stops_when_page_smaller_than_batch(self):
        messages = [{"id": "1", "author": {"username": "a"}, "content": "hi", "timestamp": "t"}]
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value=messages)
        get_mock = AsyncMock(return_value=resp)
        client = _fake_client(get_mock)
        config = _make_config({"bot_token": "tok", "channel_ids": ["chan1"], "batch_size": 100})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(DiscordConnector().get_delta(config, None))
        assert len(docs) == 1
        assert get_mock.await_count == 1  # single page since len(messages) < batch_size

    async def test_pagination_continues_full_pages(self):
        full_page = [
            {"id": str(i), "author": {"username": "a"}, "content": f"m{i}", "timestamp": "t"}
            for i in range(2)
        ]
        empty_page: list = []
        get_mock = AsyncMock()
        resp_full = MagicMock(is_success=True)
        resp_full.json = MagicMock(return_value=full_page)
        resp_empty = MagicMock(is_success=True)
        resp_empty.json = MagicMock(return_value=empty_page)
        get_mock.side_effect = [resp_full, resp_empty]
        client = _fake_client(get_mock)
        config = _make_config({"bot_token": "tok", "channel_ids": ["chan1"], "batch_size": 2})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(DiscordConnector().get_delta(config, None))
        assert len(docs) == 2
        assert get_mock.await_count == 2

    async def test_message_without_id_is_skipped(self):
        messages = [{"author": {"username": "bob"}, "content": "no id here", "timestamp": "t"}]
        resp = MagicMock(is_success=True)
        resp.json = MagicMock(return_value=messages)
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"bot_token": "tok", "channel_ids": ["chan1"]})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(DiscordConnector().get_delta(config, None))
        assert docs == []

    async def test_cursor_used_as_after_param(self):
        resp = MagicMock(is_success=True)
        resp.json = MagicMock(return_value=[])
        get_mock = AsyncMock(return_value=resp)
        client = _fake_client(get_mock)
        config = _make_config({"bot_token": "tok", "channel_ids": ["chan1"]})
        with patch("httpx.AsyncClient", return_value=client):
            await _collect(DiscordConnector().get_delta(config, "555"))
        _args, kwargs = get_mock.await_args_list[0]
        assert kwargs["params"]["after"] == "555"
