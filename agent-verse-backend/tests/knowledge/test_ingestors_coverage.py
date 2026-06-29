"""Comprehensive coverage for all app/knowledge/ingestors/*.

Mocks all external HTTP calls — no real GitHub/Confluence/Jira/Slack
API keys needed. Tests chunking logic, error handling, and all branches.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── GitHubIngestor ────────────────────────────────────────────────────────────

class TestGitHubIngestorInit:
    def test_token_from_constructor(self):
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor(token="ghp_abc123")
        assert ing._token == "ghp_abc123"

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        ing = GitHubIngestor()
        assert ing._token == "env-token"

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

class TestPdfIngestor:
    def setup_method(self):
        from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
        self.ing = PdfIngestor()

    def test_pypdf_not_installed_returns_placeholder(self):
        saved = sys.modules.get("pypdf")
        sys.modules["pypdf"] = None  # type: ignore
        try:
            from app.knowledge.ingestors import pdf_ingestor
            import importlib
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            chunks = ing.extract_chunks(content=b"%PDF-1.4 dummy", filename="test.pdf")
        finally:
            if saved is not None:
                sys.modules["pypdf"] = saved
            else:
                sys.modules.pop("pypdf", None)

        assert len(chunks) == 1
        assert "pypdf" in chunks[0]["content"].lower() or "PDF" in chunks[0]["content"]
        assert chunks[0]["source_type"] == "pdf"

    def test_extract_chunks_success(self):
        """Mocked pypdf returns text chunks."""
        long_text = "This is page content. " * 60  # > 1000 chars → multiple chunks

        mock_page = MagicMock()
        mock_page.extract_text.return_value = long_text

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page, mock_page]  # 2 pages

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader = MagicMock(return_value=mock_reader)

        saved = sys.modules.get("pypdf")
        sys.modules["pypdf"] = mock_pypdf  # type: ignore
        try:
            from app.knowledge.ingestors import pdf_ingestor
            import importlib
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            chunks = ing.extract_chunks(
                content=b"fake pdf bytes",
                filename="report.pdf",
                source_url="http://s3.com/report.pdf",
            )
        finally:
            if saved is not None:
                sys.modules["pypdf"] = saved
            else:
                sys.modules.pop("pypdf", None)

        assert len(chunks) > 0
        assert chunks[0]["source_type"] == "pdf"
        assert chunks[0]["source_doc_id"] == "report.pdf"
        assert chunks[0]["page_number"] in (1, 2)
        assert chunks[0]["source_url"] == "http://s3.com/report.pdf"
        assert chunks[0]["metadata"]["filename"] == "report.pdf"

    def test_extract_chunks_short_page_skipped(self):
        """Pages with < 30 chars of text are skipped."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Too short"  # < 30 chars

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader = MagicMock(return_value=mock_reader)

        saved = sys.modules.get("pypdf")
        sys.modules["pypdf"] = mock_pypdf  # type: ignore
        try:
            from app.knowledge.ingestors import pdf_ingestor
            import importlib
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            chunks = ing.extract_chunks(content=b"pdf", filename="short.pdf")
        finally:
            if saved is not None:
                sys.modules["pypdf"] = saved
            else:
                sys.modules.pop("pypdf", None)

        assert chunks == []

    def test_extract_chunks_none_text_skipped(self):
        """Page returning None for text is handled."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader = MagicMock(return_value=mock_reader)

        saved = sys.modules.get("pypdf")
        sys.modules["pypdf"] = mock_pypdf  # type: ignore
        try:
            from app.knowledge.ingestors import pdf_ingestor
            import importlib
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            chunks = ing.extract_chunks(content=b"pdf", filename="null_page.pdf")
        finally:
            if saved is not None:
                sys.modules["pypdf"] = saved
            else:
                sys.modules.pop("pypdf", None)

        assert chunks == []

    def test_extract_chunks_exception_returns_empty(self):
        """Exception during extraction returns empty list."""
        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader = MagicMock(side_effect=Exception("corrupted PDF"))

        saved = sys.modules.get("pypdf")
        sys.modules["pypdf"] = mock_pypdf  # type: ignore
        try:
            from app.knowledge.ingestors import pdf_ingestor
            import importlib
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            chunks = ing.extract_chunks(content=b"corrupt", filename="bad.pdf")
        finally:
            if saved is not None:
                sys.modules["pypdf"] = saved
            else:
                sys.modules.pop("pypdf", None)

        assert chunks == []


# ── DocxIngestor ─────────────────────────────────────────────────────────────

class TestDocxIngestor:
    def test_python_docx_not_installed_returns_placeholder(self):
        saved = sys.modules.get("docx")
        sys.modules["docx"] = None  # type: ignore
        try:
            from app.knowledge.ingestors import docx_ingestor
            import importlib
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            chunks = ing.extract_chunks(content=b"fake docx", filename="test.docx")
        finally:
            if saved is not None:
                sys.modules["docx"] = saved
            else:
                sys.modules.pop("docx", None)

        assert len(chunks) == 1
        assert "docx" in chunks[0]["content"].lower() or "DOCX" in chunks[0]["content"]

    def test_extract_chunks_success(self):
        """Happy path with mocked python-docx."""
        para_texts = [
            "This is the first paragraph with substantial content for testing.",
            "This is the second paragraph with more content that should be chunked.",
            "Third paragraph adds more text to ensure we hit the chunk threshold.",
        ]

        mock_paras = []
        for text in para_texts:
            p = MagicMock()
            p.text = text
            mock_paras.append(p)

        mock_doc = MagicMock()
        mock_doc.paragraphs = mock_paras

        mock_docx = MagicMock()
        mock_docx.Document = MagicMock(return_value=mock_doc)

        saved = sys.modules.get("docx")
        sys.modules["docx"] = mock_docx  # type: ignore
        try:
            from app.knowledge.ingestors import docx_ingestor
            import importlib
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            chunks = ing.extract_chunks(
                content=b"fake docx bytes",
                filename="doc.docx",
                source_url="http://s3.com/doc.docx",
            )
        finally:
            if saved is not None:
                sys.modules["docx"] = saved
            else:
                sys.modules.pop("docx", None)

        assert len(chunks) > 0
        assert chunks[0]["source_type"] == "docx"
        assert chunks[0]["source_doc_id"] == "doc.docx"
        assert chunks[0]["source_url"] == "http://s3.com/doc.docx"

    def test_extract_chunks_skips_short_paragraphs(self):
        """Paragraphs shorter than 20 chars are filtered out."""
        mock_paras = []
        for text in ["Hi", "OK", "Short"]:  # all < 20 chars
            p = MagicMock()
            p.text = text
            mock_paras.append(p)

        mock_doc = MagicMock()
        mock_doc.paragraphs = mock_paras

        mock_docx = MagicMock()
        mock_docx.Document = MagicMock(return_value=mock_doc)

        saved = sys.modules.get("docx")
        sys.modules["docx"] = mock_docx  # type: ignore
        try:
            from app.knowledge.ingestors import docx_ingestor
            import importlib
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            chunks = ing.extract_chunks(content=b"docx", filename="short.docx")
        finally:
            if saved is not None:
                sys.modules["docx"] = saved
            else:
                sys.modules.pop("docx", None)

        assert chunks == []

    def test_extract_chunks_exception_returns_empty(self):
        """Exception returns empty list."""
        mock_docx = MagicMock()
        mock_docx.Document = MagicMock(side_effect=Exception("bad docx"))

        saved = sys.modules.get("docx")
        sys.modules["docx"] = mock_docx  # type: ignore
        try:
            from app.knowledge.ingestors import docx_ingestor
            import importlib
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            chunks = ing.extract_chunks(content=b"corrupt", filename="bad.docx")
        finally:
            if saved is not None:
                sys.modules["docx"] = saved
            else:
                sys.modules.pop("docx", None)

        assert chunks == []


# ── ConfluenceIngestor ───────────────────────────────────────────────────────

class TestConfluenceIngestor:
    def _make(self):
        from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor
        return ConfluenceIngestor(
            base_url="http://confluence.example.com",
            token="token123",
            user="admin@example.com",
        )

    def test_html_to_text_strips_tags(self):
        from app.knowledge.ingestors.confluence_ingestor import _html_to_text
        result = _html_to_text("<p>Hello <b>World</b></p>")
        assert "<" not in result
        assert "Hello" in result
        assert "World" in result

    def test_html_to_text_decodes_entities(self):
        from app.knowledge.ingestors.confluence_ingestor import _html_to_text
        result = _html_to_text("&lt;code&gt; &amp; entity &gt;")
        assert "<code>" in result
        assert "&" in result

    def test_html_to_text_normalises_whitespace(self):
        from app.knowledge.ingestors.confluence_ingestor import _html_to_text
        result = _html_to_text("a  <br/>  b")
        assert "  " not in result  # multiple spaces collapsed

    @pytest.mark.asyncio
    async def test_ingest_space_empty_returns_no_chunks(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_space("EMPTY")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_space_with_page_content(self):
        """Pages with body content are chunked."""
        page_html = "<p>" + "Hello world. " * 60 + "</p>"
        pages = {
            "results": [
                {
                    "id": "p001",
                    "title": "Getting Started",
                    "body": {"storage": {"value": page_html}},
                    "space": {"name": "Engineering"},
                }
            ]
        }
        first_resp = MagicMock()
        first_resp.raise_for_status = MagicMock()
        first_resp.json.return_value = pages

        # Second call returns empty to stop pagination
        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[first_resp, empty_resp])

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_space("MYSPACE")

        assert len(chunks) > 0
        assert chunks[0]["source_type"] == "confluence"
        assert chunks[0]["metadata"]["space_key"] == "MYSPACE"
        assert chunks[0]["metadata"]["title"] == "Getting Started"

    @pytest.mark.asyncio
    async def test_ingest_space_skips_short_content(self):
        """Pages with text < 50 chars after stripping HTML are skipped."""
        pages = {
            "results": [
                {
                    "id": "p002",
                    "title": "Empty",
                    "body": {"storage": {"value": "<p>Hi</p>"}},
                    "space": {"name": "Test"},
                }
            ]
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = pages

        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp, empty_resp])

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_space("TEST")

        assert chunks == []


# ── JiraIngestor ─────────────────────────────────────────────────────────────

class TestJiraIngestor:
    def _make(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        return JiraIngestor(
            base_url="http://jira.example.com",
            token="jira-token",
            user="user@jira.com",
        )

    def test_make_basic_encoding(self):
        import base64
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        result = JiraIngestor._make_basic("user@x.com", "mytoken")
        decoded = base64.b64decode(result).decode()
        assert decoded == "user@x.com:mytoken"

    def test_adf_to_text_plain_string(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        result = JiraIngestor._adf_to_text("plain text")
        assert result == "plain text"

    def test_adf_to_text_text_node(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        adf = {"type": "text", "text": "Hello World"}
        assert JiraIngestor._adf_to_text(adf) == "Hello World"

    def test_adf_to_text_paragraph(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        adf = {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "First sentence."},
                {"type": "text", "text": " Second sentence."},
            ],
        }
        result = JiraIngestor._adf_to_text(adf)
        assert "First sentence." in result
        assert "Second sentence." in result

    def test_adf_to_text_nested(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Nested text"}],
                }
            ],
        }
        result = JiraIngestor._adf_to_text(adf)
        assert "Nested text" in result

    def test_adf_to_text_non_dict(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        result = JiraIngestor._adf_to_text(42)
        assert result == "42"

    @pytest.mark.asyncio
    async def test_ingest_project_empty(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_project("EMPTY")

        assert chunks == []

    @pytest.mark.asyncio
    async def test_ingest_project_with_issues(self):
        issue = {
            "key": "PROJ-1",
            "fields": {
                "summary": "Fix the critical bug in login flow",
                "description": "When users try to login with SSO enabled and the "
                               "Keycloak server is down, the error message is unclear. "
                               "We need better error handling here.",
                "status": {"name": "In Progress"},
                "priority": {"name": "Critical"},
                "comment": {"comments": []},
            },
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": [issue]}

        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = {"issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp, empty_resp])

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_project("PROJ")

        assert len(chunks) >= 1
        assert chunks[0]["source_type"] == "jira"
        assert "PROJ-1" in chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_ingest_project_adf_description(self):
        """ADF-format description is converted to text."""
        adf_desc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "The authentication service fails when "
                                    "Redis connection is dropped mid-request.",
                        }
                    ],
                }
            ],
        }
        issue = {
            "key": "PROJ-2",
            "fields": {
                "summary": "Auth service Redis failure",
                "description": adf_desc,
                "status": {"name": "Open"},
                "priority": {"name": "High"},
                "comment": {"comments": []},
            },
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": [issue]}

        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = {"issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp, empty_resp])

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_project("PROJ")

        assert len(chunks) >= 1
        assert "Redis" in chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_ingest_project_includes_comments(self):
        """Comments create additional chunks."""
        issue = {
            "key": "PROJ-3",
            "fields": {
                "summary": "Improve error messages in the UI",
                "description": "Users are confused by the current error messages "
                               "when the API returns 500 errors. Need clearer text.",
                "status": {"name": "Open"},
                "priority": {"name": "Medium"},
                "comment": {
                    "comments": [
                        {
                            "body": "I agree, this is a long comment about why "
                                    "we should fix this issue right away. The users "
                                    "keep complaining about the vague error messages.",
                        }
                    ]
                },
            },
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": [issue]}

        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json.return_value = {"issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp, empty_resp])

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_project("PROJ")

        # issue chunk + comment chunk
        assert len(chunks) >= 2
        comment_chunks = [c for c in chunks if "Comment on" in c["content"]]
        assert len(comment_chunks) >= 1

    @pytest.mark.asyncio
    async def test_ingest_project_with_jql_extra(self):
        """jql_extra is appended to the JQL query."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        ing = self._make()
        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = await ing.ingest_project(
                "PROJ", jql_extra="status = Open", max_issues=10
            )

        # Verify that the JQL parameter was passed (check the call args)
        call_kwargs = mock_client.get.call_args_list[0]
        params = call_kwargs.kwargs.get("params", {})
        assert "Open" in params.get("jql", "")


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
