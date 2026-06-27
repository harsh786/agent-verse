"""Web search tool using SearXNG (self-hosted open-source search aggregator).

Falls back to DuckDuckGo Instant Answer API if SearXNG not configured.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = ""


@dataclass
class WebSearchResult:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    error: str | None = None
    source: str = "searxng"


class WebSearchTool:
    """Web search via SearXNG (self-hosted) or DuckDuckGo fallback.

    SearXNG: SEARXNG_URL env var (e.g. http://searxng:8080)
    Timeout: SEARXNG_TIMEOUT_SECONDS (default 10)
    """

    name = "web_search"
    description = "Search the web for current information, documentation, or facts."

    def __init__(self) -> None:
        self._searxng_url = os.getenv("SEARXNG_URL", "").rstrip("/")
        self._timeout = float(os.getenv("SEARXNG_TIMEOUT_SECONDS", "10"))

    async def search(self, query: str, *, num_results: int = 5) -> WebSearchResult:
        """Search the web. Returns up to num_results results."""
        if self._searxng_url:
            return await self._search_searxng(query, num_results=num_results)
        return await self._search_duckduckgo(query, num_results=num_results)

    async def _search_searxng(self, query: str, *, num_results: int) -> WebSearchResult:
        url = f"{self._searxng_url}/search"
        params = {"q": query, "format": "json", "pageno": 1}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    source=r.get("engine", "searxng"),
                )
                for r in data.get("results", [])[:num_results]
            ]
            return WebSearchResult(query=query, results=results, source="searxng")
        except Exception as exc:
            logger.warning("searxng_search_failed", query=query, error=str(exc))
            return await self._search_duckduckgo(query, num_results=num_results)

    async def _search_duckduckgo(self, query: str, *, num_results: int) -> WebSearchResult:
        """DuckDuckGo Instant Answer API — no key required."""
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            results: list[SearchResult] = []
            # Abstract (featured snippet)
            if data.get("AbstractText"):
                results.append(SearchResult(
                    title=data.get("Heading", query),
                    url=data.get("AbstractURL", ""),
                    snippet=data["AbstractText"][:500],
                    source="duckduckgo",
                ))
            # Related topics
            for topic in data.get("RelatedTopics", [])[:num_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(SearchResult(
                        title=topic.get("Text", "")[:80],
                        url=topic.get("FirstURL", ""),
                        snippet=topic.get("Text", "")[:300],
                        source="duckduckgo",
                    ))
                if len(results) >= num_results:
                    break

            return WebSearchResult(query=query, results=results, source="duckduckgo")
        except Exception as exc:
            logger.warning("duckduckgo_search_failed", query=query, error=str(exc))
            return WebSearchResult(query=query, results=[], error=str(exc), source="duckduckgo")

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "default": 5, "description": "Number of results"},
                },
                "required": ["query"],
            },
        }

    async def execute(self, *, query: str, num_results: int = 5) -> dict:
        result = await self.search(query, num_results=num_results)
        if result.error:
            return {"error": result.error, "results": []}
        return {
            "query": result.query,
            "source": result.source,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in result.results
            ],
        }
