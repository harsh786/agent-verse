"""Full-text search across chat sessions using in-memory substring matching.

Production uses the GIN index `idx_chat_messages_fts` on Postgres with
`to_tsvector` + `ts_headline` snippets. This implementation serves tests
and the in-memory app path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchResult:
    message_id: str
    session_id: str
    role: str
    snippet: str       # highlighted excerpt
    created_at: str


class ChatSearchEngine:
    """Substring search over in-memory message store.

    In production this calls:
        SELECT id, session_id, role,
               ts_headline('english', content, websearch_to_tsquery($1)) AS snippet,
               created_at
        FROM chat_messages
        WHERE tenant_id = $2
          AND to_tsvector('english', content) @@ websearch_to_tsquery('english', $1)
        LIMIT $3
    """

    def cross_session_search(
        self,
        query: str,
        messages: list[dict],
        tenant_id: str,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Return messages matching *query* across all sessions for *tenant_id*."""
        return self._search(query, messages, tenant_id, session_id=None, limit=limit)

    def within_session_search(
        self,
        query: str,
        messages: list[dict],
        tenant_id: str,
        session_id: str,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Return messages matching *query* within *session_id*."""
        return self._search(query, messages, tenant_id, session_id=session_id, limit=limit)

    # ── Private ────────────────────────────────────────────────────────────

    def _search(
        self,
        query: str,
        messages: list[dict],
        tenant_id: str,
        session_id: str | None,
        limit: int,
    ) -> list[SearchResult]:
        q = query.lower().strip()
        results: list[SearchResult] = []
        for m in messages:
            if m.get("tenant_id") != tenant_id:
                continue
            if session_id and m.get("session_id") != session_id:
                continue
            content: str = m.get("content", "")
            if q not in content.lower():
                continue
            results.append(
                SearchResult(
                    message_id=m["id"],
                    session_id=m["session_id"],
                    role=m["role"],
                    snippet=self._make_snippet(content, q),
                    created_at=str(m.get("created_at", "")),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _make_snippet(self, content: str, query: str, context: int = 80) -> str:
        """Return a short snippet with the query term highlighted."""
        idx = content.lower().find(query)
        if idx == -1:
            return content[:160]
        start = max(0, idx - context)
        end = min(len(content), idx + len(query) + context)
        snippet = content[start:end]
        # Bold the match (markdown style)
        matched = content[idx : idx + len(query)]
        return snippet.replace(matched, f"**{matched}**")
