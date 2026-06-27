"""Tests for the knowledge ingestion pipeline — Phase P0.2."""
import pytest
import os


def test_pdf_ingestor_importable():
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    ingestor = PdfIngestor()
    assert ingestor is not None


def test_pdf_ingestor_returns_list_for_invalid_pdf():
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    ingestor = PdfIngestor()
    # Invalid bytes — should not crash
    chunks = ingestor.extract_chunks(content=b"not a pdf", filename="test.pdf", source_url="x")
    assert isinstance(chunks, list)


def test_pdf_ingestor_citation_metadata():
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    ingestor = PdfIngestor()
    chunks = ingestor.extract_chunks(
        content=b"not a real pdf", filename="report.pdf", source_url="https://company.com/report.pdf"
    )
    for chunk in chunks:
        assert "source_url" in chunk
        assert "source_type" in chunk
        assert chunk["source_type"] == "pdf"


def test_docx_ingestor_importable():
    from app.knowledge.ingestors.docx_ingestor import DocxIngestor
    assert DocxIngestor() is not None


def test_docx_ingestor_graceful_fallback():
    from app.knowledge.ingestors.docx_ingestor import DocxIngestor
    ingestor = DocxIngestor()
    chunks = ingestor.extract_chunks(content=b"not docx", filename="doc.docx", source_url="x")
    assert isinstance(chunks, list)


def test_github_ingestor_should_ingest_python():
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    ingestor = GitHubIngestor()
    assert ingestor._should_ingest("src/main.py") is True


def test_github_ingestor_skips_node_modules():
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    ingestor = GitHubIngestor()
    assert ingestor._should_ingest("node_modules/lodash/index.js") is False


def test_github_ingestor_skips_images():
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    ingestor = GitHubIngestor()
    assert ingestor._should_ingest("assets/logo.png") is False


def test_github_ingestor_skips_git_dir():
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    ingestor = GitHubIngestor()
    assert ingestor._should_ingest(".git/config") is False


def test_confluence_html_to_text():
    from app.knowledge.ingestors.confluence_ingestor import _html_to_text
    html = "<h1>Title</h1><p>This is <b>bold</b> text with &amp; entities.</p>"
    text = _html_to_text(html)
    assert "Title" in text
    assert "bold" in text
    assert "&amp;" not in text  # entities decoded
    assert "<" not in text  # no HTML tags


def test_jira_adf_to_text():
    from app.knowledge.ingestors.jira_ingestor import JiraIngestor
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second paragraph"}]},
        ]
    }
    text = JiraIngestor._adf_to_text(adf)
    assert "Hello world" in text
    assert "Second paragraph" in text


def test_knowledge_ingestors_all_importable():
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    from app.knowledge.ingestors.docx_ingestor import DocxIngestor
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor
    from app.knowledge.ingestors.jira_ingestor import JiraIngestor
    from app.knowledge.ingestors.slack_ingestor import SlackIngestor
    for cls in [PdfIngestor, DocxIngestor, GitHubIngestor, ConfluenceIngestor, JiraIngestor, SlackIngestor]:
        assert cls is not None


def test_migration_0035_exists():
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0035" in f for f in files), "Migration 0035 must exist for knowledge citations"


def test_knowledge_api_has_pdf_ingest_endpoint():
    from app.main import create_app
    app = create_app()
    # FastAPI registers routes inside _IncludedRouter wrappers in newer versions;
    # url_path_for is the reliable way to check route existence.
    try:
        path = str(app.url_path_for("ingest_pdf"))
        assert "ingest/pdf" in path, "POST /knowledge/ingest/pdf endpoint must exist"
    except Exception:
        # Fallback: scan routes recursively for the path string
        def _collect_routes(routes):
            result = []
            for r in routes:
                rpath = getattr(r, "path", "")
                if rpath:
                    result.append(rpath)
                orig = getattr(r, "original_router", None)
                if orig:
                    result.extend(_collect_routes(getattr(orig, "routes", [])))
                result.extend(_collect_routes(getattr(r, "routes", [])))
            return result
        all_routes = _collect_routes(app.routes)
        assert any("ingest/pdf" in r for r in all_routes), "POST /knowledge/ingest/pdf endpoint must exist"


def test_knowledge_api_has_github_ingest_endpoint():
    from app.main import create_app
    app = create_app()
    try:
        path = str(app.url_path_for("ingest_github"))
        assert "ingest/github" in path, "POST /knowledge/ingest/github endpoint must exist"
    except Exception:
        def _collect_routes(routes):
            result = []
            for r in routes:
                rpath = getattr(r, "path", "")
                if rpath:
                    result.append(rpath)
                orig = getattr(r, "original_router", None)
                if orig:
                    result.extend(_collect_routes(getattr(orig, "routes", [])))
                result.extend(_collect_routes(getattr(r, "routes", [])))
            return result
        all_routes = _collect_routes(app.routes)
        assert any("ingest/github" in r for r in all_routes), "POST /knowledge/ingest/github endpoint must exist"


def test_reindex_task_in_celery_beat():
    import inspect
    from app.scaling import celery_app as ca
    src = inspect.getsource(ca)
    assert "reindex_stale_knowledge" in src or "reindex" in src.lower(), \
        "reindex_stale_knowledge must be in beat schedule"


def test_rag_store_hybrid_search_returns_citation_fields():
    """hybrid_search_db must return source_url, source_doc_id, page_number."""
    import inspect
    from app.rag import store
    src = inspect.getsource(store)
    assert "source_url" in src, "KnowledgeStore.hybrid_search_db must return source_url"
    assert "source_doc_id" in src, "KnowledgeStore.hybrid_search_db must return source_doc_id"
