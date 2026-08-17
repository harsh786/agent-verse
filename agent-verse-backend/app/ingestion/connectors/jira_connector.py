"""JiraConnector — Atlassian Jira issue and comment ingestion.

Cursor: last issue updatedDate (ISO 8601).
Supports: Jira Cloud and Jira Server/Data Center.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("jira", feature_flag="ingestion_connector_jira_enabled")
class JiraConnector(BaseConnector):
    """Atlassian Jira connector — issues, comments, and attachments via REST API."""

    source_type = "jira"
    supports_acl_propagation = True

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import httpx
            cc = config.connection_config
            base_url = cc.get("base_url", "").rstrip("/")
            auth = (cc.get("username", ""), cc.get("api_token", ""))
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base_url}/rest/api/3/myself", auth=auth)
                r.raise_for_status()
                user = r.json()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"user": user.get("displayName"), "email": user.get("emailAddress")},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        base_url = cc.get("base_url", "").rstrip("/")
        auth = (cc.get("username", ""), cc.get("api_token", ""))
        project_keys = cc.get("project_keys") or []
        batch_size = int(cc.get("batch_size", 50))
        include_comments = cc.get("include_comments", True)

        new_cursor = cursor or ""

        # Build JQL
        parts: list[str] = []
        if project_keys:
            joined = ", ".join(f'"{k}"' for k in project_keys)
            parts.append(f"project in ({joined})")
        if cursor:
            parts.append(f"updated > '{cursor}'")
        jql = " AND ".join(parts) if parts else "ORDER BY updated ASC"
        if "ORDER BY" not in jql:
            jql += " ORDER BY updated ASC"

        async with httpx.AsyncClient(timeout=30) as client:
            start = 0
            while True:
                r = await client.get(
                    f"{base_url}/rest/api/3/search",
                    params={
                        "jql": jql,
                        "maxResults": batch_size,
                        "startAt": start,
                        "fields": "summary,description,comment,updated,status,assignee,reporter,priority,issuetype",
                    },
                    auth=auth,
                )
                if not r.is_success:
                    _log.warning("jira: search failed %d: %s", r.status_code, r.text[:200])
                    break

                data = r.json()
                issues = data.get("issues", [])
                if not issues:
                    break

                for issue in issues:
                    key = issue.get("key", "")
                    fields = issue.get("fields", {})
                    updated = fields.get("updated", "")
                    new_cursor = max(new_cursor, updated)
                    summary = fields.get("summary", "")
                    desc = _extract_adf_text(fields.get("description") or {})
                    status = fields.get("status", {}).get("name", "")
                    assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
                    itype = fields.get("issuetype", {}).get("name", "")

                    text_parts = [
                        f"Issue: {key} [{itype}] — {summary}",
                        f"Status: {status}  Assignee: {assignee}",
                        f"Updated: {updated}",
                    ]
                    if desc:
                        text_parts.append(f"\nDescription:\n{desc}")

                    if include_comments:
                        comments = fields.get("comment", {}).get("comments", [])
                        for c in comments[-20:]:  # last 20 comments
                            author = (c.get("author") or {}).get("displayName", "?")
                            body = _extract_adf_text(c.get("body") or {})
                            text_parts.append(f"\n[Comment by {author}]: {body}")

                    full_text = "\n".join(text_parts)
                    doc = RawDocument(
                        doc_id=str(uuid.uuid4()),
                        source_id=config.source_id,
                        tenant_id=config.tenant_id,
                        source_url=f"{base_url}/browse/{key}",
                        content=full_text.encode(),
                        content_type="text/plain",
                        metadata={"key": key, "status": status, "updated": updated, "type": itype},
                    )
                    yield doc, new_cursor

                start += len(issues)
                if start >= data.get("total", 0):
                    break


def _extract_adf_text(node: dict | str | None) -> str:
    """Recursively extract plain text from Atlassian Document Format (ADF)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    parts: list[str] = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content") or []:
        parts.append(_extract_adf_text(child))
    return " ".join(p for p in parts if p)
