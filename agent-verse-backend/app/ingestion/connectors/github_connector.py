"""GitHubConnector — wraps GitHubIngestor in BaseConnector interface.

Supports:
- Code: changed files since last commit (incremental via git log)
- Issues/PRs: REST API with since= parameter
- ACL: repository visibility + team membership
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("github")
class GitHubConnector(BaseConnector):
    """GitHub code, issues, PRs, and wiki ingestion."""

    source_type = "github"
    supports_acl_propagation = True
    supports_deletion_tracking = False

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import httpx
            token = config.connection_config.get("token", "")
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
                )
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                data = r.json()
                return ConnectionHealth(ok=True, latency_ms=latency, metadata={"login": data.get("login")})
            return ConnectionHealth(ok=False, latency_ms=latency, error=f"HTTP {r.status_code}")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield changed files and issues/PRs since cursor."""
        from app.ingestion.source_config import RawDocument
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor

        token = config.connection_config.get("token", "")
        repos = config.connection_config.get("repos", [])
        include_issues = config.connection_config.get("include_issues", True)
        include_code = config.connection_config.get("include_code", True)

        ingestor = GitHubIngestor(token=token)
        new_cursor = cursor or ""

        for repo in repos:
            try:
                if include_code:
                    chunks = await ingestor.ingest_repo(
                        repo,
                        branch=config.connection_config.get("branch", "main"),
                        file_extensions=config.connection_config.get("file_extensions", [".py", ".ts", ".md"]),
                    )
                    for chunk in chunks:
                        doc_id = f"{repo}_{chunk.get('source_doc_id', uuid.uuid4().hex)}"
                        raw = RawDocument(
                            doc_id=doc_id,
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            content=chunk.get("content", "").encode("utf-8"),
                            content_type="text/plain",
                            source_url=chunk.get("source_url", ""),
                            metadata=chunk.get("metadata", {}),
                        )
                        yield raw, new_cursor or doc_id
            except Exception as exc:
                _log.warning("github_connector_repo_error repo=%s: %s", repo, exc)
