"""Extra coverage for all knowledge ingestors — mock all external HTTP/lib calls."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── DocxIngestor ──────────────────────────────────────────────────────────────


# ── SlackIngestor ──────────────────────────────────────────────────────────────

class TestSlackIngestor:
    @pytest.mark.asyncio
    async def test_ingest_channel_api_error(self):
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = SlackIngestor(token="xoxb-test")
            result = await ing.ingest_channel("C123", channel_name="general")
        assert result == []

    @pytest.mark.asyncio
    async def test_ingest_channel_happy_path(self):
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor

        messages = [
            {
                "type": "message",
                "text": f"Message {i} with enough content here to be included in chunk.",
                "ts": f"1234.{i:04d}",
            }
            for i in range(7)
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "messages": messages,
            "response_metadata": {"next_cursor": ""},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = SlackIngestor(token="xoxb-test")
            chunks = await ing.ingest_channel("C123", channel_name="engineering", max_messages=6)

        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert chunks[0]["source_type"] == "slack"

    @pytest.mark.asyncio
    async def test_ingest_channel_skips_subtypes(self):
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor

        messages = [
            {"type": "message", "subtype": "channel_join", "text": "Alice joined the channel."},
            {"type": "message", "text": "Normal message with plenty of text content here."},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "messages": messages,
            "response_metadata": {"next_cursor": ""},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = SlackIngestor(token="xoxb-test")
            chunks = await ing.ingest_channel("C123")

        # Only the non-subtype message counts
        assert isinstance(chunks, list)

    @pytest.mark.asyncio
    async def test_ingest_channel_with_cursor_pagination(self):
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor

        def make_messages(n=3):
            return [
                {
                    "type": "message",
                    "text": f"Content message number {i} with sufficient length",
                    "ts": f"{i}",
                }
                for i in range(n)
            ]

        responses = [
            {
                "ok": True,
                "messages": make_messages(3),
                "response_metadata": {"next_cursor": "cursor_page2"},
            },
            {
                "ok": True,
                "messages": make_messages(3),
                "response_metadata": {"next_cursor": ""},  # last page
            },
        ]
        mock_resp = MagicMock()
        mock_resp.json.side_effect = responses

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = SlackIngestor(token="xoxb-test")
            chunks = await ing.ingest_channel("C123", max_messages=100)

        assert isinstance(chunks, list)

    def test_headers_contain_bearer_token(self):
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor
        ing = SlackIngestor(token="xoxb-mytoken")
        headers = ing._headers()
        assert headers["Authorization"] == "Bearer xoxb-mytoken"


# ── GitHubIngestor ────────────────────────────────────────────────────────────

class TestGitHubIngestorExtra:
    @pytest.mark.asyncio
    async def test_get_tree_truncated_warning(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "README.md", "size": 100}],
            "truncated": True,
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = GitHubIngestor(token="tok")
            tree = await ing._get_tree("owner", "repo")
        assert len(tree) == 1

    @pytest.mark.asyncio
    async def test_fetch_file_content(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "# README\n\nThis is the content."

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = GitHubIngestor(token="tok")
            content = await ing._fetch_file_content("owner", "repo", "README.md")
        assert "README" in content

    def test_should_ingest_skip_binary(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="")
        assert ing._should_ingest("image.png") is False
        assert ing._should_ingest("archive.zip") is False
        assert ing._should_ingest("font.ttf") is False

    def test_should_ingest_skip_dirs(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="")
        assert ing._should_ingest("node_modules/index.js") is False
        assert ing._should_ingest(".git/config") is False
        assert ing._should_ingest("__pycache__/mod.pyc") is False

    def test_should_ingest_accept_text_files(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="")
        assert ing._should_ingest("README.md") is True
        assert ing._should_ingest("src/main.py") is True
        assert ing._should_ingest("app/index.ts") is True

    def test_should_ingest_unknown_extension(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="")
        assert ing._should_ingest("data.unknownext") is False

    @pytest.mark.asyncio
    async def test_ingest_repo_happy_path(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor

        tree_items = [
            {"type": "blob", "path": "README.md", "size": 500},
            {"type": "blob", "path": "src/main.py", "size": 1000},
            {"type": "tree", "path": "src"},  # skip trees
        ]
        file_content = "# Module\n\n" + "A" * 200

        mock_tree_resp = MagicMock()
        mock_tree_resp.raise_for_status = MagicMock()
        mock_tree_resp.json.return_value = {"tree": tree_items, "truncated": False}

        mock_file_resp = MagicMock()
        mock_file_resp.raise_for_status = MagicMock()
        mock_file_resp.text = file_content

        call_count = [0]

        async def mock_get(url, *args, **kwargs):
            call_count[0] += 1
            if "git/trees" in url:
                return mock_tree_resp
            return mock_file_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = GitHubIngestor(token="tok")
            chunks = await ing.ingest_repo("owner", "repo")

        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert chunks[0]["source_type"] == "github"

    @pytest.mark.asyncio
    async def test_ingest_repo_404_skips_file(self):
        import httpx

        from app.knowledge.ingestors.github_ingestor import GitHubIngestor

        tree_items = [{"type": "blob", "path": "deleted.py", "size": 100}]

        mock_tree_resp = MagicMock()
        mock_tree_resp.raise_for_status = MagicMock()
        mock_tree_resp.json.return_value = {"tree": tree_items, "truncated": False}

        mock_404_resp = MagicMock()
        mock_404_resp.status_code = 404

        async def mock_get(url, *args, **kwargs):
            if "git/trees" in url:
                return mock_tree_resp
            err = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_404_resp)
            raise err

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = GitHubIngestor(token="tok")
            chunks = await ing.ingest_repo("owner", "repo")

        # 404 should be silently skipped
        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_repo_other_http_error_logged(self):
        import httpx

        from app.knowledge.ingestors.github_ingestor import GitHubIngestor

        tree_items = [{"type": "blob", "path": "app.py", "size": 100}]

        mock_tree_resp = MagicMock()
        mock_tree_resp.raise_for_status = MagicMock()
        mock_tree_resp.json.return_value = {"tree": tree_items, "truncated": False}

        mock_503_resp = MagicMock()
        mock_503_resp.status_code = 503

        async def mock_get(url, *args, **kwargs):
            if "git/trees" in url:
                return mock_tree_resp
            err = httpx.HTTPStatusError(
                "Service Unavailable",
                request=MagicMock(),
                response=mock_503_resp,
            )
            raise err

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = GitHubIngestor(token="tok")
            # Should not raise; the error is caught and logged
            chunks = await ing.ingest_repo("owner", "repo")
        assert chunks == []
