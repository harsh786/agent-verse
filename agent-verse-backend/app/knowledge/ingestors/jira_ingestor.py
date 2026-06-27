"""Jira issue ingestor via REST API v3."""
from __future__ import annotations
import re
from typing import Any
import httpx
from app.observability.logging import get_logger

logger = get_logger(__name__)


class JiraIngestor:
    def __init__(self, base_url: str, token: str, user: str) -> None:
        self._base = base_url.rstrip("/")
        self._auth = (user, token)
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {self._make_basic(user, token)}",
        }

    @staticmethod
    def _make_basic(user: str, token: str) -> str:
        import base64
        return base64.b64encode(f"{user}:{token}".encode()).decode()

    async def ingest_project(
        self, project_key: str, jql_extra: str = "", max_issues: int = 500
    ) -> list[dict[str, Any]]:
        jql = f"project = {project_key}"
        if jql_extra:
            jql += f" AND {jql_extra}"
        jql += " ORDER BY updated DESC"

        chunks = []
        start = 0

        while len(chunks) // 2 < max_issues:  # rough estimate
            params = {"jql": jql, "startAt": start, "maxResults": 50,
                      "fields": "summary,description,status,assignee,priority,comment,labels,created,updated"}
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{self._base}/rest/api/3/search",
                               params=params, headers=self._headers)
                r.raise_for_status()
                data = r.json()
                issues = data.get("issues", [])
                if not issues:
                    break

                for issue in issues:
                    key = issue["key"]
                    fields = issue.get("fields", {})
                    summary = fields.get("summary", "")
                    desc = fields.get("description", "") or ""
                    # Jira description can be ADF (JSON) or plain text
                    if isinstance(desc, dict):
                        desc = self._adf_to_text(desc)
                    status = fields.get("status", {}).get("name", "")
                    priority = fields.get("priority", {}).get("name", "")

                    # Build issue text
                    issue_text = (
                        f"Issue: {key}\nTitle: {summary}\n"
                        f"Status: {status} | Priority: {priority}\n\n"
                        f"{desc[:2000]}"
                    ).strip()

                    if len(issue_text) >= 50:
                        chunks.append({
                            "content": issue_text,
                            "source_url": f"{self._base}/browse/{key}",
                            "source_type": "jira",
                            "source_doc_id": key,
                            "page_number": None,
                            "metadata": {"key": key, "summary": summary,
                                         "status": status, "project": project_key},
                        })

                    # Include comments as separate chunks
                    for comment in (fields.get("comment", {}) or {}).get("comments", [])[:5]:
                        body = comment.get("body", "") or ""
                        if isinstance(body, dict):
                            body = self._adf_to_text(body)
                        if len(body.strip()) >= 50:
                            chunks.append({
                                "content": f"Comment on {key}:\n{body[:1000]}",
                                "source_url": f"{self._base}/browse/{key}",
                                "source_type": "jira",
                                "source_doc_id": f"{key}/comment",
                                "page_number": None,
                                "metadata": {"key": key, "type": "comment"},
                            })

                start += len(issues)
                if len(issues) < 50:
                    break

        return chunks

    @staticmethod
    def _adf_to_text(adf: dict) -> str:
        """Convert Atlassian Document Format to plain text (recursive)."""
        if not isinstance(adf, dict):
            return str(adf)
        node_type = adf.get("type", "")
        if node_type == "text":
            return adf.get("text", "")
        parts = []
        for child in adf.get("content", []):
            parts.append(JiraIngestor._adf_to_text(child))
        sep = "\n" if node_type in ("paragraph", "heading", "bulletList", "orderedList") else " "
        return sep.join(filter(None, parts))
