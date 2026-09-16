"""Tests for SalesforceConnector — OAuth password-grant + SOQL ingestion."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.salesforce_connector import SalesforceConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-sf",
        tenant_id="t1",
        name="Test Salesforce",
        family="crm_erp",
        source_type="salesforce",
        connection_config=conn_config or {},
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


def _fake_client(get_impl=None, post_impl=None):
    client = AsyncMock()
    if get_impl is not None:
        client.get = get_impl
    if post_impl is not None:
        client.post = post_impl
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _auth_response():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={"access_token": "tok-123", "instance_url": "https://acme.my.salesforce.com"}
    )
    return resp


class TestAuthenticate:
    async def test_authenticate_returns_token_and_instance(self):
        auth_resp = _auth_response()
        client = _fake_client(post_impl=AsyncMock(return_value=auth_resp))
        config = _make_config({"username": "u", "password": "p", "security_token": "st"})
        with patch("httpx.AsyncClient", return_value=client):
            token, instance = await SalesforceConnector()._authenticate(config)
        assert token == "tok-123"
        assert instance == "https://acme.my.salesforce.com"


class TestValidateConnection:
    async def test_success(self):
        auth_resp = _auth_response()
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()

        auth_client = _fake_client(post_impl=AsyncMock(return_value=auth_resp))
        api_client = _fake_client(get_impl=AsyncMock(return_value=api_resp))
        config = _make_config({"username": "u", "password": "p"})

        with patch("httpx.AsyncClient", side_effect=[auth_client, api_client]):
            result = await SalesforceConnector().validate_connection(config)

        assert result.ok is True
        assert result.metadata["instance"] == "https://acme.my.salesforce.com"

    async def test_auth_failure(self):
        client = _fake_client(post_impl=AsyncMock(side_effect=Exception("invalid_grant")))
        config = _make_config({"username": "bad"})
        with patch("httpx.AsyncClient", return_value=client):
            result = await SalesforceConnector().validate_connection(config)
        assert result.ok is False
        assert "invalid_grant" in result.error


class TestGetFields:
    async def test_get_fields_filters_binary_types(self):
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(
            return_value={
                "fields": [
                    {"name": "Id", "type": "id"},
                    {"name": "Name", "type": "string"},
                    {"name": "Photo", "type": "base64"},
                    {"name": "Secret", "type": "encryptedstring"},
                ]
            }
        )
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        fields = await SalesforceConnector._get_fields(client, "https://x", "tok", "Contact")
        assert fields == ["Id", "Name"]

    async def test_get_fields_failure_returns_default(self):
        resp = MagicMock(is_success=False)
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        fields = await SalesforceConnector._get_fields(client, "https://x", "tok", "Contact")
        assert fields == ["Id", "Name", "CreatedDate", "SystemModstamp"]


class TestGetDelta:
    async def test_yields_records_with_explicit_fields(self):
        auth_resp = _auth_response()
        query_resp = MagicMock()
        query_resp.is_success = True
        query_resp.json = MagicMock(
            return_value={
                "records": [
                    {
                        "attributes": {"type": "Account"},
                        "Id": "001",
                        "Name": "Acme Inc",
                        "SystemModstamp": "2026-01-05T00:00:00Z",
                    }
                ]
            }
        )
        auth_client = _fake_client(post_impl=AsyncMock(return_value=auth_resp))
        query_client = _fake_client(get_impl=AsyncMock(return_value=query_resp))
        config = _make_config(
            {
                "username": "u",
                "password": "p",
                "sobjects": ["Account"],
                "fields": {"Account": ["Id", "Name", "SystemModstamp"]},
            }
        )

        with patch("httpx.AsyncClient", side_effect=[auth_client, query_client]):
            docs = await _collect(SalesforceConnector().get_delta(config, None))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert "Acme Inc" in doc.content.decode()
        assert doc.metadata["sobject"] == "Account"
        assert cursor == "2026-01-05T00:00:00Z"
        assert "attributes" not in doc.content.decode()

    async def test_pagination_follows_next_records_url(self):
        auth_resp = _auth_response()
        page1 = MagicMock(is_success=True)
        page1.json = MagicMock(
            return_value={
                "records": [
                    {"attributes": {}, "Id": "1", "SystemModstamp": "2026-01-01T00:00:00Z"}
                ],
                "nextRecordsUrl": "/services/data/v58.0/query/next-page",
            }
        )
        page2 = MagicMock(is_success=True)
        page2.json = MagicMock(
            return_value={
                "records": [
                    {"attributes": {}, "Id": "2", "SystemModstamp": "2026-01-02T00:00:00Z"}
                ]
            }
        )
        get_mock = AsyncMock(side_effect=[page1, page2])
        auth_client = _fake_client(post_impl=AsyncMock(return_value=auth_resp))
        query_client = _fake_client(get_impl=get_mock)
        config = _make_config(
            {
                "username": "u",
                "password": "p",
                "sobjects": ["Contact"],
                "fields": {"Contact": ["Id", "SystemModstamp"]},
            }
        )
        with patch("httpx.AsyncClient", side_effect=[auth_client, query_client]):
            docs = await _collect(SalesforceConnector().get_delta(config, None))

        assert len(docs) == 2
        assert get_mock.await_count == 2
        second_url = get_mock.await_args_list[1].args[0]
        assert second_url == "https://acme.my.salesforce.com/services/data/v58.0/query/next-page"

    async def test_query_http_failure_stops_sobject(self):
        auth_resp = _auth_response()
        fail_resp = MagicMock(is_success=False, status_code=400)
        auth_client = _fake_client(post_impl=AsyncMock(return_value=auth_resp))
        query_client = _fake_client(get_impl=AsyncMock(return_value=fail_resp))
        config = _make_config(
            {"username": "u", "password": "p", "sobjects": ["Lead"], "fields": {"Lead": ["Id"]}}
        )
        with patch("httpx.AsyncClient", side_effect=[auth_client, query_client]):
            docs = await _collect(SalesforceConnector().get_delta(config, None))
        assert docs == []

    async def test_cursor_adds_systemmodstamp_filter(self):
        auth_resp = _auth_response()
        resp = MagicMock(is_success=True)
        resp.json = MagicMock(return_value={"records": []})
        get_mock = AsyncMock(return_value=resp)
        auth_client = _fake_client(post_impl=AsyncMock(return_value=auth_resp))
        query_client = _fake_client(get_impl=get_mock)
        config = _make_config(
            {
                "username": "u",
                "password": "p",
                "sobjects": ["Case"],
                "fields": {"Case": ["Id", "SystemModstamp"]},
            }
        )
        with patch("httpx.AsyncClient", side_effect=[auth_client, query_client]):
            await _collect(SalesforceConnector().get_delta(config, "2026-01-01T00:00:00Z"))
        _args, kwargs = get_mock.await_args_list[0]
        assert "WHERE SystemModstamp > 2026-01-01T00:00:00Z" in kwargs["params"]["q"]
