"""Tests for SentryConnector — issue/event ingestion with Link-header pagination."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.sentry_connector import SentryConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-sentry",
        tenant_id="t1",
        name="Test Sentry",
        family="observability",
        source_type="sentry",
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
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"auth_token": "tok"})
        with patch("httpx.AsyncClient", return_value=client):
            result = await SentryConnector().validate_connection(config)
        assert result.ok is True
        assert result.metadata["api"] == "sentry.io"

    async def test_failure_raises(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=Exception("401 unauthorized"))
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"auth_token": "bad"})
        with patch("httpx.AsyncClient", return_value=client):
            result = await SentryConnector().validate_connection(config)
        assert result.ok is False
        assert "unauthorized" in result.error


class TestGetDelta:
    async def test_yields_issues_and_tracks_cursor(self):
        issue1 = {
            "id": "1",
            "title": "NullPointerException",
            "lastSeen": "2026-01-02T00:00:00Z",
            "level": "error",
            "count": 5,
            "culprit": "app.module",
            "permalink": "https://sentry.io/issues/1/",
        }
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value=[issue1])
        resp.headers = {"Link": ""}
        client = _fake_client(AsyncMock(return_value=resp))

        config = _make_config(
            {"auth_token": "tok", "org_slug": "acme", "project_slugs": ["backend"]}
        )
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(SentryConnector().get_delta(config, None))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert "NullPointerException" in doc.content.decode()
        assert doc.source_url == "https://sentry.io/issues/1/"
        assert cursor == "2026-01-02T00:00:00Z"
        assert doc.metadata["project"] == "backend"

    async def test_pagination_follows_link_header(self):
        issue_a = {"id": "a", "title": "A", "lastSeen": "2026-01-01T00:00:00Z"}
        issue_b = {"id": "b", "title": "B", "lastSeen": "2026-01-02T00:00:00Z"}

        page1 = MagicMock()
        page1.is_success = True
        page1.json = MagicMock(return_value=[issue_a])
        page1.headers = {
            "Link": '<https://sentry.io/api/0/next>; rel="next"; results="true"'
        }
        page2 = MagicMock()
        page2.is_success = True
        page2.json = MagicMock(return_value=[issue_b])
        page2.headers = {"Link": ""}

        get_mock = AsyncMock(side_effect=[page1, page2])
        client = _fake_client(get_mock)
        config = _make_config({"auth_token": "tok", "org_slug": "acme"})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(SentryConnector().get_delta(config, None))

        assert len(docs) == 2
        assert get_mock.await_count == 2
        second_call_url = get_mock.await_args_list[1].args[0]
        assert second_call_url == "https://sentry.io/api/0/next"

    async def test_http_failure_stops_iteration(self):
        resp = MagicMock()
        resp.is_success = False
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"auth_token": "tok", "org_slug": "acme"})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(SentryConnector().get_delta(config, None))
        assert docs == []

    async def test_empty_issue_list_stops_iteration(self):
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value=[])
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"auth_token": "tok", "org_slug": "acme"})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(SentryConnector().get_delta(config, None))
        assert docs == []

    async def test_cursor_appends_lastseen_filter_to_query(self):
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value=[])
        get_mock = AsyncMock(return_value=resp)
        client = _fake_client(get_mock)
        config = _make_config({"auth_token": "tok", "org_slug": "acme"})
        with patch("httpx.AsyncClient", return_value=client):
            await _collect(SentryConnector().get_delta(config, "2026-01-01T00:00:00Z"))
        _args, kwargs = get_mock.await_args_list[0]
        assert "lastSeen:>2026-01-01T00:00:00Z" in kwargs["params"]["query"]
