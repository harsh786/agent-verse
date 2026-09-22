"""Tests for JiraConnector — validate_connection, JQL construction, paginated
get_delta, ADF description/comment extraction, auth failures, malformed
query responses, and empty result sets. httpx is mocked throughout."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.jira_connector import JiraConnector, _extract_adf_text
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-j",
        tenant_id="t1",
        name="Test Jira",
        family="document_store",
        source_type="jira",
        enabled=True,
        connection_config=conn_config
        or {
            "base_url": "https://acme.atlassian.net",
            "username": "u@acme.com",
            "api_token": "tok",
        },
    )


def _mock_async_client(get_impl):
    client = AsyncMock()
    client.get = get_impl
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _issue(key, *, summary="Bug title", updated="2026-01-01T00:00:00.000+0000", comments=None):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": {"type": "doc", "content": [{"type": "text", "text": "desc body"}]},
            "updated": updated,
            "status": {"name": "Open"},
            "assignee": {"displayName": "Alice"},
            "issuetype": {"name": "Bug"},
            "comment": {"comments": comments or []},
        },
    }


class TestValidateConnection:
    async def test_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"displayName": "Alice", "emailAddress": "a@acme.com"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            health = await JiraConnector().validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["user"] == "Alice"

    async def test_auth_failure_raises_http_status_error(self):
        import httpx

        resp = MagicMock(status_code=401)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=resp
        )

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            health = await JiraConnector().validate_connection(_make_config())
        assert health.ok is False
        assert "Unauthorized" in health.error

    async def test_network_exception(self):
        async def get(*a, **kw):
            raise ConnectionError("timeout")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            health = await JiraConnector().validate_connection(_make_config())
        assert health.ok is False
        assert "timeout" in health.error


class TestGetDeltaPagination:
    async def test_single_page_result(self):
        page = {"issues": [_issue("PROJ-1")], "total": 1}

        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"project_keys": ["PROJ"]})
            results = [d async for d in JiraConnector().get_delta(config, None)]

        assert len(results) == 1
        doc, cursor = results[0]
        assert "PROJ-1" in doc.content.decode()
        assert doc.metadata["key"] == "PROJ-1"
        assert cursor == "2026-01-01T00:00:00.000+0000"

    async def test_paginates_across_multiple_batches(self):
        batch1 = {"issues": [_issue("PROJ-1"), _issue("PROJ-2")], "total": 3}
        batch2 = {"issues": [_issue("PROJ-3")], "total": 3}
        call_count = 0

        async def get(url, params=None, auth=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = batch1 if call_count == 1 else batch2
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"batch_size": 2})
            results = [d async for d in JiraConnector().get_delta(config, None)]

        assert call_count == 2
        assert len(results) == 3
        keys = {d.metadata["key"] for d, _c in results}
        assert keys == {"PROJ-1", "PROJ-2", "PROJ-3"}

    async def test_resuming_from_cursor_adds_updated_clause_to_jql(self):
        page = {"issues": [_issue("PROJ-5")], "total": 1}
        seen_jql = []

        async def get(url, params=None, auth=None):
            seen_jql.append(params["jql"])
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"project_keys": ["PROJ"]})
            _ = [d async for d in JiraConnector().get_delta(config, "2026-01-01T00:00:00.000+0000")]

        assert "updated > '2026-01-01T00:00:00.000+0000'" in seen_jql[0]

    async def test_pagination_params_advance_start_at(self):
        batch1 = {"issues": [_issue("PROJ-1")], "total": 2}
        batch2 = {"issues": [_issue("PROJ-2")], "total": 2}
        seen_start_at = []

        async def get(url, params=None, auth=None):
            seen_start_at.append(params["startAt"])
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = batch1 if len(seen_start_at) == 1 else batch2
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"batch_size": 1})
            _ = [d async for d in JiraConnector().get_delta(config, None)]

        assert seen_start_at == [0, 1]


class TestGetDeltaEmptyAndErrors:
    async def test_empty_result_set_yields_nothing(self):
        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = {"issues": [], "total": 0}
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            results = [d async for d in JiraConnector().get_delta(_make_config(), None)]
        assert results == []

    async def test_malformed_jql_400_response_breaks_loop_gracefully(self):
        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = False
            resp.status_code = 400
            resp.text = "Error in the JQL Query: 'foo' is not a valid field"
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            # An invalid project key still flows into a JQL string; the
            # connector must not crash on a 400 — it should stop cleanly.
            config = _make_config({"project_keys": ["!!!not-a-real-project!!!"]})
            results = [d async for d in JiraConnector().get_delta(config, None)]
        assert results == []

    async def test_auth_failure_401_during_search_breaks_loop_gracefully(self):
        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = False
            resp.status_code = 401
            resp.text = "Unauthorized"
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            results = [d async for d in JiraConnector().get_delta(_make_config(), None)]
        assert results == []


class TestGetDeltaContentAndCursor:
    async def test_comments_included_when_enabled(self):
        comments = [{"author": {"displayName": "Bob"}, "body": "LGTM"}]
        page = {"issues": [_issue("PROJ-1", comments=comments)], "total": 1}

        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"include_comments": True})
            results = [d async for d in JiraConnector().get_delta(config, None)]
        assert "LGTM" in results[0][0].content.decode()
        assert "Bob" in results[0][0].content.decode()

    async def test_comments_excluded_when_disabled(self):
        comments = [{"author": {"displayName": "Bob"}, "body": "LGTM"}]
        page = {"issues": [_issue("PROJ-1", comments=comments)], "total": 1}

        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"include_comments": False})
            results = [d async for d in JiraConnector().get_delta(config, None)]
        assert "LGTM" not in results[0][0].content.decode()

    async def test_unassigned_issue_shows_unassigned(self):
        page = {
            "issues": [
                {
                    "key": "PROJ-9",
                    "fields": {
                        "summary": "No owner",
                        "description": None,
                        "updated": "2026-01-01T00:00:00.000+0000",
                        "status": {"name": "Open"},
                        "assignee": None,
                        "issuetype": {"name": "Task"},
                        "comment": {"comments": []},
                    },
                }
            ],
            "total": 1,
        }

        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = page
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            results = [d async for d in JiraConnector().get_delta(_make_config(), None)]
        assert "Unassigned" in results[0][0].content.decode()

    async def test_cursor_grows_with_max_updated_across_pages(self):
        batch1 = {"issues": [_issue("PROJ-1", updated="2026-01-01T00:00:00.000+0000")], "total": 2}
        batch2 = {"issues": [_issue("PROJ-2", updated="2026-01-05T00:00:00.000+0000")], "total": 2}
        call_count = 0

        async def get(url, params=None, auth=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.is_success = True
            resp.json.return_value = batch1 if call_count == 1 else batch2
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            config = _make_config({"batch_size": 1})
            results = [d async for d in JiraConnector().get_delta(config, None)]

        assert results[-1][1] == "2026-01-05T00:00:00.000+0000"


class TestExtractADFText:
    def test_plain_string_passthrough(self):
        assert _extract_adf_text("plain text") == "plain text"

    def test_none_returns_empty(self):
        assert _extract_adf_text(None) == ""

    def test_non_dict_non_string_returns_empty(self):
        assert _extract_adf_text(42) == ""

    def test_nested_content_extracted(self):
        node = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "world"}]},
            ],
        }
        result = _extract_adf_text(node)
        assert "hello" in result
        assert "world" in result


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert JiraConnector().source_type == "jira"
    assert get_connector("jira") is JiraConnector
    assert JiraConnector.supports_acl_propagation is True
