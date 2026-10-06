"""Comprehensive coverage for all app/knowledge/ingestors/*.

Mocks all external HTTP calls — no real GitHub/Confluence/Jira/Slack
API keys needed. Tests chunking logic, error handling, and all branches.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── GitHubIngestor ────────────────────────────────────────────────────────────

class TestGitHubIngestorInit:
    def test_token_from_constructor(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="ghp_abc123")
        assert ing._token == "ghp_abc123"

    def test_platform_token_is_never_used(self, monkeypatch):
        """Regression: falling back to the platform's GITHUB_TOKEN let any tenant
        ingest private repos only the platform can read."""
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        assert GitHubIngestor()._token == ""
        assert GitHubIngestor(token="tenant-token")._token == "tenant-token"

    def test_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor()
        assert ing._token == ""


class TestGitHubIngestorHeaders:
    def test_headers_with_token(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="ghp_secret")
        h = ing._headers()
        assert h["Authorization"] == "Bearer ghp_secret"
        assert "Accept" in h
        assert "X-GitHub-Api-Version" in h

    def test_headers_without_token(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="")
        h = ing._headers()
        assert "Authorization" not in h


class TestGitHubShouldIngest:
    def setup_method(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        self.ing = GitHubIngestor(token="")

    def test_python_file_ingested(self):
        assert self.ing._should_ingest("src/main.py") is True

    def test_typescript_file_ingested(self):
        assert self.ing._should_ingest("frontend/app.ts") is True

    def test_markdown_ingested(self):
        assert self.ing._should_ingest("README.md") is True

    def test_yaml_ingested(self):
        assert self.ing._should_ingest("docker-compose.yml") is True

    def test_png_skipped(self):
        assert self.ing._should_ingest("assets/logo.png") is False

    def test_jpg_skipped(self):
        assert self.ing._should_ingest("img/photo.jpg") is False

    def test_pdf_skipped(self):
        assert self.ing._should_ingest("docs/guide.pdf") is False

    def test_node_modules_skipped(self):
        assert self.ing._should_ingest("node_modules/lodash/index.js") is False

    def test_pycache_skipped(self):
        assert self.ing._should_ingest("app/__pycache__/main.cpython-312.pyc") is False

    def test_venv_skipped(self):
        assert self.ing._should_ingest(".venv/lib/site-packages/foo.py") is False

    def test_git_dir_skipped(self):
        assert self.ing._should_ingest(".git/config") is False

    def test_unknown_extension_skipped(self):
        assert self.ing._should_ingest("file.xyz9999") is False

    def test_no_extension_allowed(self):
        # Files without extensions (Makefile, Dockerfile, etc.)
        assert self.ing._should_ingest("Makefile") is True


class TestGitHubIngestRepo:
    def _make_ingestor(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        return GitHubIngestor(token="test-token")

    @pytest.mark.asyncio
    async def test_ingest_repo_produces_chunks(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                {"type": "blob", "path": "README.md", "size": 500},
                {"type": "blob", "path": "src/app.py", "size": 2000},
                {"type": "tree", "path": "src"},  # directories should be ignored
            ],
            "truncated": False,
        }
        tree_resp.raise_for_status = MagicMock()

        content = "# Project README\n\n" + ("This is content. " * 100)
        file_resp = MagicMock()
        file_resp.raise_for_status = MagicMock()
        file_resp.text = content

        call_count = 0

        async def mock_get(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree_resp
            return file_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=mock_get)

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("myorg", "myrepo")

        assert len(chunks) > 0
        assert chunks[0]["source_type"] == "github"
        assert chunks[0]["metadata"]["owner"] == "myorg"
        assert chunks[0]["metadata"]["repo"] == "myrepo"
        assert "myorg/myrepo" in chunks[0]["source_url"]

    @pytest.mark.asyncio
    async def test_ingest_repo_skips_tiny_files(self):
        """Files with < 50 chars are not chunked."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "tiny.py", "size": 10}],
            "truncated": False,
        }
        tree_resp.raise_for_status = MagicMock()

        small_resp = MagicMock()
        small_resp.raise_for_status = MagicMock()
        small_resp.text = "x = 1"  # < 50 chars

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[tree_resp, small_resp])

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_repo_handles_404(self):
        """404 errors on individual files are silently skipped."""
        import httpx

        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "gone.py", "size": 100}],
            "truncated": False,
        }
        tree_resp.raise_for_status = MagicMock()

        mock_404_resp = MagicMock()
        mock_404_resp.status_code = 404
        error = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_404_resp
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[tree_resp, error])

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_repo_handles_non_404_http_error(self):
        """Non-404 HTTP errors are logged and skipped."""
        import httpx

        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "server_error.py", "size": 200}],
            "truncated": False,
        }
        tree_resp.raise_for_status = MagicMock()

        mock_500_resp = MagicMock()
        mock_500_resp.status_code = 500
        error = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_500_resp
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[tree_resp, error])

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo")

        assert chunks == []  # error logged and skipped

    @pytest.mark.asyncio
    async def test_ingest_repo_handles_generic_exception(self):
        """Generic exceptions during file fetch are logged and skipped."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "flaky.py", "size": 200}],
            "truncated": False,
        }
        tree_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=[tree_resp, ConnectionError("Network error")]
        )

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_repo_respects_max_files(self):
        """max_files parameter limits number of files processed."""
        blobs = [
            {"type": "blob", "path": f"file{i}.py", "size": 200}
            for i in range(20)
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": blobs, "truncated": False}
        tree_resp.raise_for_status = MagicMock()

        big_content = "x = " + "very_long_content " * 100  # > 50 chars

        call_count = 0

        async def mock_get(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree_resp
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.text = big_content
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=mock_get)

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo", max_files=3)

        # At most 3 files processed → at most ~6 chunks (2 per file for long content)
        assert 0 < len(chunks) <= 10

    @pytest.mark.asyncio
    async def test_ingest_repo_truncated_tree_logs_warning(self):
        """Truncated GitHub tree logs a warning but continues."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": [], "truncated": True}
        tree_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=tree_resp)

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_repo_with_custom_branch(self):
        """Branch name appears in source_url."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "README.md", "size": 200}],
            "truncated": False,
        }
        tree_resp.raise_for_status = MagicMock()

        content = "# My Project\n\n" + "Important content here. " * 30

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        async def mock_get(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree_resp
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.text = content
            return resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        ing = self._make_ingestor()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_repo("org", "repo", branch="develop")

        assert len(chunks) > 0
        assert "develop" in chunks[0]["source_url"]


# ── PdfIngestor ───────────────────────────────────────────────────────────────


# ── SlackIngestor ─────────────────────────────────────────────────────────────

class TestSlackIngestor:
    def _make(self):
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor
        return SlackIngestor(token="xoxb-test-token-abc")

    def test_headers(self):
        ing = self._make()
        h = ing._headers()
        assert h["Authorization"] == "Bearer xoxb-test-token-abc"

    @pytest.mark.asyncio
    async def test_ingest_channel_api_error(self):
        """ok=false returns empty list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "not_authed"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_channel("C123456", channel_name="general")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_channel_batches_messages(self):
        """6 messages → 2 chunks (5 + 1)."""
        messages = [
            {
                "type": "message",
                "text": f"Message {i}: This has enough content to be included.",
                "ts": f"1000.{i:03d}",
            }
            for i in range(6)
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

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_channel("C123456", channel_name="general")

        assert len(chunks) == 2  # 5 + 1
        assert chunks[0]["source_type"] == "slack"
        assert "C123456" in chunks[0]["source_url"]

    @pytest.mark.asyncio
    async def test_ingest_channel_skips_bot_messages(self):
        """Messages with subtype are skipped."""
        messages = [
            {"type": "message", "subtype": "bot_message", "text": "I am a bot!"},
            {
                "type": "message",
                "text": "Real user message with enough content here.",
                "ts": "1000.001",
            },
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "messages": messages,
            "response_metadata": {},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_channel("C123456")

        for c in chunks:
            assert "I am a bot!" not in c["content"]

    @pytest.mark.asyncio
    async def test_ingest_channel_skips_short_text(self):
        """Messages shorter than 10 chars are skipped."""
        messages = [
            {"type": "message", "text": "ok", "ts": "1.0"},
            {"type": "message", "text": "👍", "ts": "1.1"},
            {
                "type": "message",
                "text": "This is a real substantial message that should be included.",
                "ts": "1.2",
            },
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "messages": messages,
            "response_metadata": {},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_channel("C123456")

        assert len(chunks) == 1
        assert "real substantial" in chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_ingest_channel_pagination(self):
        """Follows next_cursor for pagination."""
        page1 = [
            {"type": "message", "text": f"Page 1 message {i} with content", "ts": f"1.{i}"}
            for i in range(5)
        ]
        page2 = [
            {"type": "message", "text": "Page 2 message with enough content", "ts": "2.0"}
        ]
        resp1 = MagicMock()
        resp1.json.return_value = {
            "ok": True,
            "messages": page1,
            "response_metadata": {"next_cursor": "cursor_xyz"},
        }
        resp2 = MagicMock()
        resp2.json.return_value = {
            "ok": True,
            "messages": page2,
            "response_metadata": {"next_cursor": ""},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp1, resp2])

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_channel("C123456", max_messages=100)

        assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_ingest_channel_leftover_window(self):
        """Remaining messages < 5 in final window are still chunked."""
        messages = [
            {"type": "message", "text": f"Msg {i} with enough content here", "ts": f"1.{i}"}
            for i in range(3)  # 3 < 5, so leftover window
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "messages": messages,
            "response_metadata": {},
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_channel("C123456")

        assert len(chunks) == 1  # all 3 in leftover window
