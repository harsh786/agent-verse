"""GitHubConnector — wraps GitHubIngestor in BaseConnector interface.

Supports:
- Code: changed files since last commit (incremental via git log)
- Issues/PRs: REST API with since= parameter
- ACL: repository visibility + team membership
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
    UnitFailures,
    stable_doc_id,
)
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
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                data = r.json()
                return ConnectionHealth(
                    ok=True, latency_ms=latency, metadata={"login": data.get("login")}
                )
            return ConnectionHealth(ok=False, latency_ms=latency, error=f"HTTP {r.status_code}")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield one document per repository file (whole) since cursor.

        Files used to be yielded as the ingestor's 1,500-character windows, all
        under the SAME document id (``<repo>_<owner/repo/path>``): the pipeline
        replaces a document id's chunks on every write, so only a file's LAST
        window stayed indexed. Each file is now one document (same id, so a
        re-sync replaces the old windows) that the pipeline chunks by its type.
        """
        from app.ingestion.source_config import RawDocument
        from app.knowledge.ingestors.github_ingestor import GitHubIngestor

        cc = config.connection_config
        token = cc.get("token", "")
        repos = cc.get("repos", [])
        include_code = cc.get("include_code", True)
        branch = cc.get("branch", "main")
        max_files = int(cc.get("max_files") or 300)

        ingestor = GitHubIngestor(token=token)
        new_cursor = cursor or ""

        failures = UnitFailures("github")
        for repo in repos:
            try:
                if include_code:
                    owner, _, repo_name = repo.partition("/")
                    file_failures: list[tuple[str, str]] = []
                    files = await ingestor.repo_files(
                        owner,
                        repo_name,
                        branch=branch,
                        max_files=max_files,
                        failures=file_failures,
                    )
                    for path, reason in file_failures:
                        failures.add(f"{repo}:{path}", reason)
                    for file in files:
                        doc_id = (
                            f"{repo}_{file['source_doc_id']}"
                            if file.get("source_doc_id")
                            else stable_doc_id(
                                config, repo, file.get("source_url", ""), file.get("path", "")
                            )
                        )
                        path = str(file.get("path") or "")
                        raw = RawDocument(
                            doc_id=doc_id,
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            content=str(file.get("content", "")).encode("utf-8"),
                            content_type="text/plain",
                            source_url=file.get("source_url", ""),
                            # The file name lets the pipeline pick the code /
                            # markdown chunker for it.
                            title=path.rsplit("/", 1)[-1],
                            metadata={**file.get("metadata", {}), "filename": path},
                        )
                        yield raw, new_cursor or doc_id
            except ConnectorUnavailableError:
                raise
            except Exception as exc:
                # USR-1: an unreadable repo (auth, not found, outage) is a counted
                # failure — it used to be logged and the sync reported success.
                failures.add(f"repo {repo}", exc)
        failures.raise_if_any()
