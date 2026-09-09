"""GitLabConnector — GitLab repository, issues, and merge request ingestion.

Mirrors the GitHubConnector interface for GitLab's REST API.
Cursor: last event/updated timestamp.
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


@register("gitlab", feature_flag="ingestion_connector_gitlab_enabled")
class GitLabConnector(BaseConnector):
    """GitLab connector — files, issues, MRs, and wikis via GitLab REST API."""

    source_type = "gitlab"
    supports_acl_propagation = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        import httpx

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            base = cc.get("base_url", "https://gitlab.com").rstrip("/")
            token = cc.get("token", "")
            headers = {"PRIVATE-TOKEN": token}
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/api/v4/user", headers=headers)
                r.raise_for_status()
                user = r.json()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"user": user.get("username"), "name": user.get("name")},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        base = cc.get("base_url", "https://gitlab.com").rstrip("/")
        token = cc.get("token", "")
        project_ids = cc.get("project_ids") or []
        ingest_types = cc.get("ingest_types") or ["issues", "merge_requests", "wiki"]
        headers = {"PRIVATE-TOKEN": token}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for project_id in project_ids:
                # Issues
                if "issues" in ingest_types:
                    params: dict = {"per_page": 50, "order_by": "updated_at", "sort": "asc"}
                    if cursor:
                        params["updated_after"] = cursor
                    url: str | None = f"{base}/api/v4/projects/{project_id}/issues"
                    while url:
                        r = await client.get(url, params=params, headers=headers)
                        if not r.is_success:
                            break
                        for issue in r.json():
                            updated = issue.get("updated_at", "")
                            new_cursor = max(new_cursor, updated)
                            text = f"# [{issue.get('iid')}] {issue.get('title')}\n\nStatus: {issue.get('state')}\nUpdated: {updated}\n\n{issue.get('description') or ''}"  # noqa: E501
                            doc = RawDocument(
                                doc_id=str(uuid.uuid4()),
                                source_id=config.source_id,
                                tenant_id=config.tenant_id,
                                source_url=issue.get("web_url", ""),
                                content=text.encode(),
                                content_type="text/plain",
                                metadata={
                                    "source_type": "gitlab",
                                    "iid": issue.get("iid"),
                                    "state": issue.get("state"),
                                    "type": "issue",
                                },
                            )
                            yield doc, new_cursor
                        next_link = r.links.get("next", {}).get("url")
                        url = next_link
                        params = {}

                # Merge Requests
                if "merge_requests" in ingest_types:
                    params = {"per_page": 50, "order_by": "updated_at", "sort": "asc"}
                    if cursor:
                        params["updated_after"] = cursor
                    url = f"{base}/api/v4/projects/{project_id}/merge_requests"
                    while url:
                        r = await client.get(url, params=params, headers=headers)
                        if not r.is_success:
                            break
                        for mr in r.json():
                            updated = mr.get("updated_at", "")
                            new_cursor = max(new_cursor, updated)
                            text = f"# MR !{mr.get('iid')}: {mr.get('title')}\n\nStatus: {mr.get('state')}\nBranch: {mr.get('source_branch')} → {mr.get('target_branch')}\n\n{mr.get('description') or ''}"  # noqa: E501
                            doc = RawDocument(
                                doc_id=str(uuid.uuid4()),
                                source_id=config.source_id,
                                tenant_id=config.tenant_id,
                                source_url=mr.get("web_url", ""),
                                content=text.encode(),
                                content_type="text/plain",
                                metadata={
                                    "source_type": "gitlab",
                                    "iid": mr.get("iid"),
                                    "state": mr.get("state"),
                                    "type": "merge_request",
                                },
                            )
                            yield doc, new_cursor
                        next_link = r.links.get("next", {}).get("url")
                        url = next_link
                        params = {}
