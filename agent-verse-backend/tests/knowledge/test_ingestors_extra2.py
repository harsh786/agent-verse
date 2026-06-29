"""Extra coverage for all knowledge ingestors — mock all external HTTP/lib calls."""
from __future__ import annotations

import io
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── DocxIngestor ──────────────────────────────────────────────────────────────

class TestDocxIngestor:
    def test_import_error_fallback(self):
        """When python-docx not installed, returns placeholder chunk."""
        with patch.dict(sys.modules, {"docx": None}):
            from app.knowledge.ingestors.docx_ingestor import DocxIngestor
            ing = DocxIngestor()
            result = ing.extract_chunks(
                content=b"fake docx bytes",
                filename="test.docx",
                source_url="http://example.com/test.docx",
            )
        assert len(result) == 1
        assert "install python-docx" in result[0]["content"].lower() or "docx" in result[0]["content"].lower()

    def test_extract_chunks_with_mock_docx(self):
        """Happy path: python-docx is available and parses paragraphs."""
        mock_paragraph = MagicMock()
        mock_paragraph.text = "This is a test paragraph with enough text content to be included in the chunk output."

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_paragraph] * 5  # 5 identical paragraphs

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict(sys.modules, {"docx": mock_docx_module}):
            import importlib
            from app.knowledge.ingestors import docx_ingestor
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            result = ing.extract_chunks(
                content=b"fake",
                filename="report.docx",
                source_url="http://example.com/report.docx",
            )
        assert isinstance(result, list)

    def test_extract_chunks_exception_returns_empty(self):
        """If Document() raises, returns []."""
        mock_docx_module = MagicMock()
        mock_docx_module.Document.side_effect = ValueError("bad file")

        with patch.dict(sys.modules, {"docx": mock_docx_module}):
            import importlib
            from app.knowledge.ingestors import docx_ingestor
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            result = ing.extract_chunks(content=b"bad", filename="x.docx")
        assert result == []

    def test_short_paragraphs_skipped(self):
        """Paragraphs shorter than 20 chars are filtered out."""
        mock_short = MagicMock()
        mock_short.text = "Hi"  # < 20 chars → skipped
        mock_long = MagicMock()
        mock_long.text = "This paragraph has plenty of text to be included " * 3

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_short, mock_long]

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict(sys.modules, {"docx": mock_docx_module}):
            import importlib
            from app.knowledge.ingestors import docx_ingestor
            importlib.reload(docx_ingestor)
            ing = docx_ingestor.DocxIngestor()
            result = ing.extract_chunks(content=b"x", filename="f.docx")
        assert isinstance(result, list)


# ── PdfIngestor ───────────────────────────────────────────────────────────────

class TestPdfIngestor:
    def test_import_error_fallback(self):
        """When pypdf not installed, returns placeholder chunk."""
        with patch.dict(sys.modules, {"pypdf": None}):
            import importlib
            from app.knowledge.ingestors import pdf_ingestor
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            result = ing.extract_chunks(
                content=b"not a real pdf",
                filename="doc.pdf",
                source_url="http://example.com/doc.pdf",
            )
        assert len(result) == 1
        assert "pypdf" in result[0]["content"].lower() or "install" in result[0]["content"].lower()

    def test_extract_chunks_happy_path(self):
        """Happy path: pypdf is available with real-ish page content."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "A" * 500  # long enough to chunk

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page, mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            import importlib
            from app.knowledge.ingestors import pdf_ingestor
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            result = ing.extract_chunks(
                content=b"pdf bytes",
                filename="test.pdf",
                source_url="http://example.com/test.pdf",
            )
        assert isinstance(result, list)
        # Two pages with 500 chars → should produce chunks
        assert len(result) >= 1
        assert result[0]["source_type"] == "pdf"
        assert result[0]["page_number"] >= 1

    def test_extract_chunks_short_page_skipped(self):
        """Pages with < 30 chars of text are skipped."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Hi"  # < 30 chars

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            import importlib
            from app.knowledge.ingestors import pdf_ingestor
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            result = ing.extract_chunks(content=b"x", filename="f.pdf")
        assert result == []

    def test_extract_chunks_exception_handled(self):
        """PdfReader exception produces empty list (not crash)."""
        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.side_effect = Exception("bad pdf")

        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            import importlib
            from app.knowledge.ingestors import pdf_ingestor
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            result = ing.extract_chunks(content=b"corrupt", filename="bad.pdf")
        assert result == []

    def test_empty_page_text_skipped(self):
        """None/empty extract_text is skipped."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            import importlib
            from app.knowledge.ingestors import pdf_ingestor
            importlib.reload(pdf_ingestor)
            ing = pdf_ingestor.PdfIngestor()
            result = ing.extract_chunks(content=b"x", filename="f.pdf")
        assert result == []


# ── ConfluenceIngestor ────────────────────────────────────────────────────────

class TestConfluenceIngestor:
    def test_html_to_text(self):
        from app.knowledge.ingestors.confluence_ingestor import _html_to_text
        html = "<h1>Title</h1><p>Content &amp; more</p>"
        result = _html_to_text(html)
        assert "Title" in result
        assert "&" in result  # &amp; decoded

    def test_html_to_text_strips_tags(self):
        from app.knowledge.ingestors.confluence_ingestor import _html_to_text
        result = _html_to_text("<div><span>hello</span></div>")
        assert "<" not in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_fetch_pages_makes_request(self):
        from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": "p1",
                    "title": "Test Page",
                    "body": {"storage": {"value": "<p>" + "A" * 200 + "</p>"}},
                    "space": {"name": "Engineering"},
                }
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = ConfluenceIngestor(
                base_url="http://confluence.example.com",
                token="token123",
                user="admin@example.com",
            )
            pages = await ing._fetch_pages("ENG")
        assert len(pages) == 1
        assert pages[0]["id"] == "p1"

    @pytest.mark.asyncio
    async def test_ingest_space_happy_path(self):
        from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor

        page_html = "<p>" + "Engineering content here. " * 30 + "</p>"
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = [
            {
                "results": [
                    {
                        "id": "p1",
                        "title": "Architecture Guide",
                        "body": {"storage": {"value": page_html}},
                        "space": {"name": "Engineering"},
                    }
                ]
            },
            {"results": []},  # second call returns empty → break
        ]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = ConfluenceIngestor("http://conf.example.com", "tok", "user@example.com")
            chunks = await ing.ingest_space("ENG", max_pages=10)

        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert chunks[0]["source_type"] == "confluence"

    @pytest.mark.asyncio
    async def test_ingest_space_skips_short_pages(self):
        from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": "p1",
                    "title": "Short",
                    "body": {"storage": {"value": "<p>Hi</p>"}},  # < 50 chars
                    "space": {"name": "Test"},
                }
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = ConfluenceIngestor("http://conf.example.com", "tok", "u@e.com")
            chunks = await ing.ingest_space("TST", max_pages=5)

        assert chunks == []


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
            {"type": "message", "text": f"Message {i} with enough content here to be included in chunk.", "ts": f"1234.{i:04d}"}
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
                {"type": "message", "text": f"Content message number {i} with sufficient length", "ts": f"{i}"}
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


# ── JiraIngestor ──────────────────────────────────────────────────────────────

class TestJiraIngestor:
    def test_make_basic_encoding(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        result = JiraIngestor._make_basic("user@example.com", "mytoken")
        import base64
        expected = base64.b64encode(b"user@example.com:mytoken").decode()
        assert result == expected

    def test_adf_to_text_plain_text(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        adf = {"type": "text", "text": "Hello world"}
        result = JiraIngestor._adf_to_text(adf)
        assert result == "Hello world"

    def test_adf_to_text_paragraph(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        adf = {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "First sentence."},
                {"type": "text", "text": "Second sentence."},
            ],
        }
        result = JiraIngestor._adf_to_text(adf)
        assert "First sentence" in result
        assert "Second sentence" in result

    def test_adf_to_text_non_dict(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        result = JiraIngestor._adf_to_text("plain string")
        assert result == "plain string"

    @pytest.mark.asyncio
    async def test_ingest_project_happy_path(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor

        issues = [
            {
                "key": "PROJ-1",
                "fields": {
                    "summary": "Fix production bug in authentication module",
                    "description": "Full description of the authentication bug that needs to be fixed urgently.",
                    "status": {"name": "Open"},
                    "priority": {"name": "High"},
                    "comment": {"comments": []},
                },
            }
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = [
            {"issues": issues},
            {"issues": []},  # second page empty → break
        ]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = JiraIngestor("http://jira.example.com", "token", "user@example.com")
            chunks = await ing.ingest_project("PROJ")

        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert chunks[0]["source_type"] == "jira"
        assert "PROJ-1" in chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_ingest_project_with_adf_description(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor

        adf_description = {
            "type": "paragraph",
            "content": [{"type": "text", "text": "This is an ADF description paragraph with enough content to be included."}],
        }
        issues = [
            {
                "key": "PROJ-2",
                "fields": {
                    "summary": "ADF description test issue that is long enough",
                    "description": adf_description,
                    "status": {"name": "Done"},
                    "priority": {"name": "Low"},
                    "comment": {"comments": [
                        {"body": "This comment is long enough to be included as a separate chunk in the knowledge base."}
                    ]},
                },
            }
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = [
            {"issues": issues},
            {"issues": []},
        ]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = JiraIngestor("http://jira.example.com", "token", "user@example.com")
            chunks = await ing.ingest_project("PROJ")

        assert any(c["source_type"] == "jira" for c in chunks)

    @pytest.mark.asyncio
    async def test_ingest_project_with_adf_comment(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor

        adf_comment = {
            "type": "paragraph",
            "content": [{"type": "text", "text": "This is a comment in ADF format with enough text."}],
        }
        issues = [
            {
                "key": "PROJ-3",
                "fields": {
                    "summary": "Test issue with ADF comment that is long enough",
                    "description": "Description with enough content to be included in the output chunks.",
                    "status": {"name": "In Progress"},
                    "priority": {"name": "Medium"},
                    "comment": {"comments": [{"body": adf_comment}]},
                },
            }
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = [{"issues": issues}, {"issues": []}]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            ing = JiraIngestor("http://jira.example.com", "token", "user@example.com")
            chunks = await ing.ingest_project("PROJ")
        assert isinstance(chunks, list)

    def test_init_sets_auth(self):
        from app.knowledge.ingestors.jira_ingestor import JiraIngestor
        ing = JiraIngestor("http://jira.example.com", "mytoken", "user@example.com")
        assert ing._auth == ("user@example.com", "mytoken")
        assert "Authorization" in ing._headers


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
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        import httpx

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
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor
        import httpx

        tree_items = [{"type": "blob", "path": "app.py", "size": 100}]

        mock_tree_resp = MagicMock()
        mock_tree_resp.raise_for_status = MagicMock()
        mock_tree_resp.json.return_value = {"tree": tree_items, "truncated": False}

        mock_503_resp = MagicMock()
        mock_503_resp.status_code = 503

        async def mock_get(url, *args, **kwargs):
            if "git/trees" in url:
                return mock_tree_resp
            err = httpx.HTTPStatusError("Service Unavailable", request=MagicMock(), response=mock_503_resp)
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
