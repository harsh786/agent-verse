"""Tests for GitHubConnector — validate_connection, get_delta orchestration
across multiple repos, and the real GitHubIngestor code path it wraps for
rate-limit (403/429) handling, private-repo auth failure, oversized-file
truncation, binary-file skipping, and malformed repo identifiers.

httpx is real and installed, so ``httpx.AsyncClient`` is patched per-test,
mirroring the pattern used by tests/knowledge/test_ingestors_coverage.py for
the underlying GitHubIngestor.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.ingestion.connectors.github_connector import GitHubConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-gh",
        tenant_id="t1",
        name="Test GitHub",
        family="code_repository",
        source_type="github",
        enabled=True,
        connection_config=conn_config
        or {"token": "gh-token", "repos": ["acme/widgets"]},
    )


def _mock_client(get_impl):
    client = AsyncMock()
    client.get = get_impl
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def _collect(agen) -> list:
    return [item async for item in agen]


def _tree_response(tree: list[dict], *, truncated: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"tree": tree, "truncated": truncated}
    return resp


def _file_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = text
    return resp


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    resp = MagicMock()
    resp.status_code = status_code
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=MagicMock(), response=resp)


class TestValidateConnection:
    async def test_success(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"login": "octocat"}

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await GitHubConnector().validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["login"] == "octocat"

    async def test_bad_token_returns_401(self):
        resp = MagicMock(status_code=401)

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await GitHubConnector().validate_connection(_make_config())
        assert health.ok is False
        assert "401" in health.error

    async def test_network_exception(self):
        async def get(*a, **kw):
            raise ConnectionError("dns failure")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            health = await GitHubConnector().validate_connection(_make_config())
        assert health.ok is False
        assert "dns failure" in health.error


class TestGetDeltaOrchestration:
    async def test_yields_docs_from_ingestor_across_multiple_repos(self):
        from app.knowledge.ingestors import github_ingestor as gi_mod

        chunks_by_repo = {
            "acme/widgets": [
                {
                    "content": "widget file content",
                    "source_url": "https://github.com/acme/widgets/blob/main/a.py",
                    "source_doc_id": "acme/widgets/a.py",
                    "metadata": {"path": "a.py"},
                }
            ],
            "acme/gadgets": [
                {
                    "content": "gadget file content",
                    "source_url": "https://github.com/acme/gadgets/blob/main/b.py",
                    "source_doc_id": "acme/gadgets/b.py",
                    "metadata": {"path": "b.py"},
                }
            ],
        }

        async def fake_ingest_repo(self, owner, repo, **kw):
            return chunks_by_repo[f"{owner}/{repo}"]

        config = _make_config({"token": "t", "repos": ["acme/widgets", "acme/gadgets"]})
        with patch.object(gi_mod.GitHubIngestor, "ingest_repo", fake_ingest_repo):
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert len(docs) == 2
        doc_ids = {d.doc_id for d, _c in docs}
        assert any("acme/widgets" in d for d in doc_ids)
        assert any("acme/gadgets" in d for d in doc_ids)

    async def test_repo_level_error_is_caught_other_repos_continue(self):
        from app.knowledge.ingestors import github_ingestor as gi_mod

        async def fake_ingest_repo(self, owner, repo, **kw):
            if repo == "broken":
                raise RuntimeError("rate limited")
            return [
                {
                    "content": "ok content",
                    "source_url": "https://github.com/acme/ok/blob/main/f.py",
                    "source_doc_id": "acme/ok/f.py",
                    "metadata": {},
                }
            ]

        config = _make_config({"token": "t", "repos": ["acme/broken", "acme/ok"]})
        with patch.object(gi_mod.GitHubIngestor, "ingest_repo", fake_ingest_repo):
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert len(docs) == 1
        assert "acme/ok" in docs[0][0].doc_id

    async def test_include_code_false_skips_ingestor_entirely(self):
        from app.knowledge.ingestors import github_ingestor as gi_mod

        called = False

        async def fake_ingest_repo(self, owner, repo, **kw):
            nonlocal called
            called = True
            return []

        config = _make_config({"token": "t", "repos": ["acme/widgets"], "include_code": False})
        with patch.object(gi_mod.GitHubIngestor, "ingest_repo", fake_ingest_repo):
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert docs == []
        assert called is False

    async def test_empty_repos_list_yields_nothing(self):
        config = _make_config({"token": "t", "repos": []})
        docs = await _collect(GitHubConnector().get_delta(config, None))
        assert docs == []


class TestGetDeltaRealIngestorPath:
    """Exercises the real GitHubIngestor via a mocked httpx.AsyncClient, so
    the connector's error handling around the *actual* GitHub REST API
    behaviour (rate limits, auth, truncation, binary skip) is covered."""

    async def test_rate_limit_403_on_tree_fetch_is_swallowed_by_connector(self):
        async def get(url, *a, **kw):
            raise _http_error(403)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))
        assert docs == []

    async def test_secondary_rate_limit_429_on_tree_fetch_is_swallowed(self):
        async def get(url, *a, **kw):
            raise _http_error(429)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))
        assert docs == []

    async def test_private_repo_auth_failure_401_on_tree_fetch_is_swallowed(self):
        async def get(url, *a, **kw):
            raise _http_error(401)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "bad-token", "repos": ["acme/private-repo"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))
        assert docs == []

    async def test_rate_limit_on_one_file_does_not_abort_remaining_files(self):
        # A 403 fetching an individual file's content is logged and skipped;
        # the ingestor keeps processing the rest of the tree.
        tree = _tree_response(
            [
                {"type": "blob", "path": "rate_limited.py", "size": 100},
                {"type": "blob", "path": "fine.py", "size": 100},
            ]
        )
        ok_content = "print('hello world')\n" * 5  # > 50 chars

        call_count = 0

        async def get(url, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree
            if "rate_limited.py" in url:
                raise _http_error(403)
            return _file_response(ok_content)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert len(docs) == 1
        assert "fine.py" in docs[0][0].metadata["path"]

    async def test_binary_file_is_never_fetched(self):
        tree = _tree_response(
            [
                {"type": "blob", "path": "logo.png", "size": 5000},
                {"type": "blob", "path": "readme.md", "size": 200},
            ]
        )
        text_content = "# Readme\n\n" + ("Documentation content. " * 20)
        fetched_urls: list[str] = []

        call_count = 0

        async def get(url, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree
            fetched_urls.append(url)
            return _file_response(text_content)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert len(docs) == 1
        assert all("logo.png" not in u for u in fetched_urls)
        assert any("readme.md" in u for u in fetched_urls)

    async def test_unrecognized_extension_is_skipped_like_binary(self):
        tree = _tree_response([{"type": "blob", "path": "data.xyz", "size": 500}])
        call_count = 0

        async def get(url, *a, **kw):
            nonlocal call_count
            call_count += 1
            return tree

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert docs == []
        assert call_count == 1  # only the tree fetch — content never requested

    async def test_oversized_file_content_is_truncated_not_rejected(self):
        # A file well over the 100KB-per-file cap is truncated (not skipped
        # entirely) at the ingestor's _fetch_file_content boundary.
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor

        huge_content = "x = 1\n" * 50_000  # 300_000 chars, well over the cap

        async def get(url, *a, **kw):
            return _file_response(huge_content)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            fetched = await GitHubIngestor(token="t")._fetch_file_content(
                "acme", "widgets", "huge.py"
            )

        assert len(fetched) == 100_000
        assert fetched == huge_content[:100_000]

    async def test_oversized_file_still_flows_through_connector_as_chunks(self):
        # End-to-end: the connector yields chunked docs for the (truncated)
        # oversized file rather than raising or silently dropping it.
        tree = _tree_response([{"type": "blob", "path": "huge.py", "size": 500_000}])
        huge_content = "x = 1\n" * 50_000

        call_count = 0

        async def get(url, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree
            return _file_response(huge_content)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert len(docs) > 1  # sliding-window chunking produced multiple docs
        assert all(d.metadata["path"] == "huge.py" for d, _c in docs)

    async def test_malformed_repo_identifier_without_slash_skipped_gracefully(self):
        # `repo.partition("/")` on a bare name yields an empty repo_name,
        # producing a malformed GitHub API URL. The real API would 404; we
        # simulate that and confirm the connector doesn't crash the batch.
        async def get(url, *a, **kw):
            raise _http_error(404)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["not-a-valid-repo-slug"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))
        assert docs == []

    async def test_truncated_tree_still_yields_available_files(self):
        tree = _tree_response(
            [{"type": "blob", "path": "partial.md", "size": 200}], truncated=True
        )
        content = "# Partial\n\n" + ("Content that made it into the truncated tree. " * 5)
        call_count = 0

        async def get(url, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tree
            return _file_response(content)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_client(get)
            config = _make_config({"token": "t", "repos": ["acme/widgets"]})
            docs = await _collect(GitHubConnector().get_delta(config, None))

        assert len(docs) == 1


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert GitHubConnector().source_type == "github"
    assert get_connector("github") is GitHubConnector
    assert GitHubConnector.supports_acl_propagation is True
    assert GitHubConnector.supports_deletion_tracking is False
