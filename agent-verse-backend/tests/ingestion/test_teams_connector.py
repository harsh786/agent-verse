"""Tests for TeamsConnector — Microsoft Teams messages via Graph API."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors.teams_connector import TeamsConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-teams",
        tenant_id="t1",
        name="teams-src",
        family=SourceFamily.COMMUNICATION,
        source_type="teams",
        connection_config=cc,
    )


def _mock_async_client(get_side_effect=None, post_side_effect=None) -> AsyncMock:
    client = AsyncMock()
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    if post_side_effect is not None:
        client.post = AsyncMock(side_effect=post_side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_get_token_posts_client_credentials_flow() -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"access_token": "tok-123"})
    client = _mock_async_client(post_side_effect=[resp])

    cfg = _config(tenant_id="tid", client_id="cid", client_secret="secret")
    with patch("httpx.AsyncClient", return_value=client):
        token = await TeamsConnector()._get_token(cfg)

    assert token == "tok-123"
    client.post.assert_called_once()
    args, kwargs = client.post.call_args
    assert "tid" in args[0]
    assert kwargs["data"]["client_id"] == "cid"
    assert kwargs["data"]["client_secret"] == "secret"
    assert kwargs["data"]["grant_type"] == "client_credentials"


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"displayName": "Alice", "mail": "alice@example.com"})
    client = _mock_async_client(get_side_effect=[resp])

    connector = TeamsConnector()
    with (
        patch.object(connector, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", return_value=client),
    ):
        health = await connector.validate_connection(_config())

    assert health.ok is True
    assert health.metadata["user"] == "Alice"
    assert health.metadata["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    connector = TeamsConnector()
    with patch.object(connector, "_get_token", AsyncMock(side_effect=RuntimeError("auth fail"))):
        health = await connector.validate_connection(_config())
    assert health.ok is False
    assert "auth fail" in health.error


@pytest.mark.asyncio
async def test_get_delta_discovers_channels_and_paginates() -> None:
    channels_resp = MagicMock()
    channels_resp.is_success = True
    channels_resp.json = MagicMock(return_value={"value": [{"id": "chan-1"}]})

    page1 = MagicMock()
    page1.is_success = True
    page1.json = MagicMock(
        return_value={
            "value": [
                {
                    "id": "m1",
                    "lastModifiedDateTime": "2026-01-01T00:00:00Z",
                    "body": {"content": "Hello team"},
                    "from": {"user": {"displayName": "Bob"}},
                    "webUrl": "https://teams/m1",
                },
                {
                    # system event message → skipped
                    "id": "m2",
                    "lastModifiedDateTime": "2026-01-02T00:00:00Z",
                    "body": {"content": "<systemEventMessage/>"},
                },
                {
                    # empty body → skipped
                    "id": "m3",
                    "lastModifiedDateTime": "2026-01-03T00:00:00Z",
                    "body": {"content": ""},
                },
            ],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
        }
    )

    page2 = MagicMock()
    page2.is_success = True
    page2.json = MagicMock(return_value={"value": []})

    client = _mock_async_client(get_side_effect=[channels_resp, page1, page2])

    connector = TeamsConnector()
    cfg = _config(team_id="team-1")
    with (
        patch.object(connector, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", return_value=client),
    ):
        docs = [d async for d in connector.get_delta(cfg, None)]

    assert len(docs) == 1
    doc, cursor = docs[0]
    assert b"Bob: Hello team" in doc.content
    assert doc.source_url == "https://teams/m1"
    assert cursor == "2026-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_get_delta_uses_explicit_channel_ids_and_cursor_filter() -> None:
    page = MagicMock()
    page.is_success = True
    page.json = MagicMock(
        return_value={
            "value": [
                {
                    "id": "m1",
                    "lastModifiedDateTime": "2026-02-01T00:00:00Z",
                    "body": {"content": "Later message"},
                    "from": {"user": {"displayName": "Carol"}},
                }
            ]
        }
    )
    client = _mock_async_client(get_side_effect=[page])

    connector = TeamsConnector()
    cfg = _config(team_id="team-1", channel_ids=["chan-9"])
    with (
        patch.object(connector, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", return_value=client),
    ):
        docs = [d async for d in connector.get_delta(cfg, "2026-01-15T00:00:00Z")]

    assert len(docs) == 1
    # Channel discovery call should have been skipped (channel_ids explicit).
    called_url = client.get.call_args_list[0].args[0]
    assert "chan-9/messages" in called_url
    assert "%24filter=lastModifiedDateTime" in called_url or "$filter" in called_url


@pytest.mark.asyncio
async def test_get_delta_breaks_on_failed_response() -> None:
    channels_resp = MagicMock()
    channels_resp.is_success = True
    channels_resp.json = MagicMock(return_value={"value": [{"id": "chan-1"}]})

    fail_resp = MagicMock()
    fail_resp.is_success = False
    fail_resp.status_code = 500

    client = _mock_async_client(get_side_effect=[channels_resp, fail_resp])

    connector = TeamsConnector()
    cfg = _config(team_id="team-1")
    with (
        patch.object(connector, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", return_value=client),
    ):
        docs = [d async for d in connector.get_delta(cfg, None)]

    assert docs == []
