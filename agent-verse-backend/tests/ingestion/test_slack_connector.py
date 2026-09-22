"""Tests for SlackConnector — validate_connection (bot-token-revoked),
get_delta orchestration across channels, and the real SlackIngestor code
path for rate limits, pagination cursors, channel-not-found, and messages
with attached files."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.slack_connector import SlackConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-s",
        tenant_id="t1",
        name="Test Slack",
        family="communication",
        source_type="slack",
        enabled=True,
        connection_config=conn_config or {"bot_token": "xoxb-tok", "channels": ["C123"]},
    )


def _mock_client(get_impl):
    client = AsyncMock()
    client.get = get_impl
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def _collect(agen) -> list:
    return [item async for item in agen]


def _msg(text: str, ts: str, *, subtype: str | None = None, msg_type: str = "message") -> dict:
    d = {"type": msg_type, "text": text, "ts": ts}
    if subtype:
        d["subtype"] = subtype
    return d


class TestValidateConnection:
    async def test_success(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": True, "team": "Acme Corp", "bot_id": "B1"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await SlackConnector().validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["workspace"] == "Acme Corp"

    async def test_bot_token_revoked(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "error": "token_revoked"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await SlackConnector().validate_connection(_make_config())
        assert health.ok is False
        assert health.error == "token_revoked"

    async def test_invalid_auth_error(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "error": "invalid_auth"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await SlackConnector().validate_connection(_make_config())
        assert health.ok is False
        assert health.error == "invalid_auth"

    async def test_network_exception(self):
        async def get(*a, **kw):
            raise ConnectionError("dns failure")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await SlackConnector().validate_connection(_make_config())
        assert health.ok is False
        assert "dns failure" in health.error


class TestGetDeltaOrchestration:
    async def test_yields_docs_across_multiple_channels(self):
        from app.knowledge.ingestors import slack_ingestor as si_mod

        async def fake_ingest_channel(self, channel_id, *, channel_name="", max_messages=500):
            return [
                {
                    "content": f"content for {channel_id}",
                    "source_url": f"https://slack.com/archives/{channel_id}",
                    "metadata": {"channel_id": channel_id, "ts": "111.000"},
                }
            ]

        config = _make_config({"bot_token": "t", "channels": ["C1", "C2"]})
        with patch.object(si_mod.SlackIngestor, "ingest_channel", fake_ingest_channel):
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert len(docs) == 2
        channel_ids = {d.metadata["channel_id"] for d, _c in docs}
        assert channel_ids == {"C1", "C2"}

    async def test_channel_error_caught_other_channels_continue(self):
        from app.knowledge.ingestors import slack_ingestor as si_mod

        async def fake_ingest_channel(self, channel_id, *, channel_name="", max_messages=500):
            if channel_id == "C-missing":
                raise RuntimeError("channel_not_found")
            return [
                {
                    "content": "ok",
                    "source_url": "https://slack.com/archives/C-ok",
                    "metadata": {"channel_id": channel_id, "ts": "111.000"},
                }
            ]

        config = _make_config({"bot_token": "t", "channels": ["C-missing", "C-ok"]})
        with patch.object(si_mod.SlackIngestor, "ingest_channel", fake_ingest_channel):
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert len(docs) == 1
        assert docs[0][0].metadata["channel_id"] == "C-ok"

    async def test_cursor_skips_already_seen_messages(self):
        from app.knowledge.ingestors import slack_ingestor as si_mod

        async def fake_ingest_channel(self, channel_id, *, channel_name="", max_messages=500):
            return [
                {"content": "old", "source_url": "u", "metadata": {"ts": "100.000"}},
                {"content": "new", "source_url": "u", "metadata": {"ts": "200.000"}},
            ]

        config = _make_config({"bot_token": "t", "channels": ["C1"]})
        with patch.object(si_mod.SlackIngestor, "ingest_channel", fake_ingest_channel):
            docs = await _collect(SlackConnector().get_delta(config, "150.000"))

        assert len(docs) == 1
        assert docs[0][0].content.decode() == "new"

    async def test_empty_channels_list_yields_nothing(self):
        config = _make_config({"bot_token": "t", "channels": []})
        docs = await _collect(SlackConnector().get_delta(config, None))
        assert docs == []

    async def test_supports_streaming_is_true(self):
        assert SlackConnector().supports_streaming is True


class TestGetDeltaRealIngestorPath:
    """Exercises the real SlackIngestor via a mocked httpx client to cover
    rate limits, pagination cursors, channel-not-found, and attachments."""

    async def test_rate_limited_response_is_handled_without_crash(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "error": "ratelimited"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"]})
            docs = await _collect(SlackConnector().get_delta(config, None))
        assert docs == []

    async def test_channel_not_found_error_yields_no_docs(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "error": "channel_not_found"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C-does-not-exist"]})
            docs = await _collect(SlackConnector().get_delta(config, None))
        assert docs == []

    async def test_thread_pagination_via_next_cursor_across_pages(self):
        page1 = {
            "ok": True,
            "messages": [_msg(f"msg {i} long enough to count", f"{100 + i}.000") for i in range(5)],
            "response_metadata": {"next_cursor": "page2token"},
        }
        page2 = {
            "ok": True,
            "messages": [_msg("final message long enough", "200.000")],
            "response_metadata": {},
        }
        call_count = 0

        async def get(url, params=None, headers=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = page1 if call_count == 1 else page2
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"], "max_messages": 500})
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert call_count == 2
        assert len(docs) >= 1
        # Cursor should have advanced to the latest message timestamp seen.
        assert docs[-1][1] == "200.000"

    async def test_bot_token_revoked_during_history_fetch_yields_no_docs(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "error": "token_revoked"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "revoked-tok", "channels": ["C123"]})
            docs = await _collect(SlackConnector().get_delta(config, None))
        assert docs == []

    async def test_max_messages_limit_stops_pagination(self):
        page = {
            "ok": True,
            "messages": [_msg(f"message number {i}", f"{i}.000") for i in range(10)],
            "response_metadata": {"next_cursor": "should-not-be-followed"},
        }

        call_count = 0

        async def get(url, params=None, headers=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"], "max_messages": 5})
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert call_count == 1  # stopped after hitting max_messages within the first page

    async def test_message_with_files_attachment_included_via_content(self):
        # Slack file-share events typically include descriptive text (e.g.
        # "shared a file: report.pdf") that is long enough to survive the
        # ingestor's 10-char minimum-length filter, so attachments surface
        # through the ordinary text content rather than being dropped.
        page = {
            "ok": True,
            "messages": [
                {
                    "type": "message",
                    "text": "shared a file: quarterly_report.pdf",
                    "ts": "100.000",
                    "files": [{"name": "quarterly_report.pdf", "filetype": "pdf"}],
                }
            ],
            "response_metadata": {},
        }

        async def get(*a, **kw):
            resp = MagicMock()
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"]})
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert len(docs) == 1
        assert "quarterly_report.pdf" in docs[0][0].content.decode()

    async def test_short_attachment_only_message_is_dropped_by_length_filter(self):
        # Documents current (possibly surprising) behaviour: a message whose
        # text is under 10 chars is filtered out regardless of any attached
        # files, since the ingestor never inspects the `files` field.
        page = {
            "ok": True,
            "messages": [
                {
                    "type": "message",
                    "text": "img.png",  # < 10 chars
                    "ts": "100.000",
                    "files": [{"name": "img.png", "filetype": "png"}],
                }
            ],
            "response_metadata": {},
        }

        async def get(*a, **kw):
            resp = MagicMock()
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"]})
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert docs == []

    async def test_cursor_skips_already_ingested_messages_end_to_end(self):
        # Regression test for a real bug: SlackConnector.get_delta reads
        # chunk["metadata"]["ts"] to decide what's already been ingested, but
        # SlackIngestor.ingest_channel used to omit "ts" from that metadata
        # dict entirely — so every incremental sync silently re-ingested the
        # whole channel regardless of the cursor. Exercised end-to-end here
        # through the real ingestor (only httpx is mocked).
        # 5 messages fill the first chunk window (flushed, ts up to 105.000);
        # a 6th message becomes its own leftover chunk (ts 200.000).
        old_messages = [_msg(f"old msg {i} with enough text", f"10{i}.000") for i in range(5)]
        new_message = [_msg("brand new message with enough text", "200.000")]
        page = {"ok": True, "messages": old_messages + new_message, "response_metadata": {}}

        async def get(*a, **kw):
            resp = MagicMock()
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"]})
            # Resuming from a cursor already past the first (old) chunk.
            docs = await _collect(SlackConnector().get_delta(config, "150.000"))

        assert len(docs) == 1
        assert "brand new message" in docs[0][0].content.decode()
        assert "old msg" not in docs[0][0].content.decode()

    async def test_bot_subtype_messages_are_skipped(self):
        page = {
            "ok": True,
            "messages": [
                _msg("a bot message here", "100.000", subtype="bot_message"),
                _msg("a real human message", "101.000"),
            ],
            "response_metadata": {},
        }

        async def get(*a, **kw):
            resp = MagicMock()
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"bot_token": "t", "channels": ["C123"]})
            docs = await _collect(SlackConnector().get_delta(config, None))

        assert len(docs) == 1
        assert "real human message" in docs[0][0].content.decode()


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert SlackConnector().source_type == "slack"
    assert get_connector("slack") is SlackConnector
    assert SlackConnector.supports_acl_propagation is True
