"""Firecrawl MCP server — web scraping, crawling, and URL discovery.

Environment:
  FIRECRAWL_API_KEY: Firecrawl API key (fc-...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"

TOOL_DEFINITIONS = [
    {
        "name": "firecrawl_scrape",
        "description": "Scrape a single URL and return clean markdown/HTML content",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to scrape"},
                "formats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["markdown"],
                    "description": "Output formats: markdown, html, rawHtml, screenshot, links",
                },
                "only_main_content": {
                    "type": "boolean",
                    "default": True,
                    "description": "Extract only the main content, removing nav/footer",
                },
                "include_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "HTML tags to include",
                },
                "exclude_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "HTML tags to exclude",
                },
                "wait_for": {
                    "type": "integer",
                    "description": "Milliseconds to wait for dynamic content",
                },
                "timeout": {
                    "type": "integer",
                    "default": 30000,
                    "description": "Timeout in milliseconds",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "firecrawl_crawl",
        "description": "Crawl an entire website and return content from all pages",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Starting URL to crawl"},
                "max_depth": {"type": "integer", "default": 2, "description": "Maximum crawl depth"},
                "limit": {"type": "integer", "default": 10, "description": "Maximum number of pages"},
                "formats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["markdown"],
                },
                "exclude_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URL patterns to exclude",
                },
                "include_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URL patterns to include (overrides excludes)",
                },
                "only_main_content": {"type": "boolean", "default": True},
                "allow_external_links": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    },
    {
        "name": "firecrawl_map",
        "description": "Discover and map all URLs on a website without crawling content",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Starting URL"},
                "search": {"type": "string", "description": "Filter URLs by search term"},
                "limit": {"type": "integer", "default": 100},
                "include_subdomains": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    },
    {
        "name": "firecrawl_batch_scrape",
        "description": "Scrape multiple URLs in a single batch request",
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to scrape",
                },
                "formats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["markdown"],
                },
                "only_main_content": {"type": "boolean", "default": True},
            },
            "required": ["urls"],
        },
    },
    {
        "name": "firecrawl_search",
        "description": "Search the web and scrape the results using Firecrawl",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "lang": {"type": "string", "default": "en"},
                "country": {"type": "string", "default": "us"},
                "scrape_options": {
                    "type": "object",
                    "description": "Scraping options for each result",
                },
            },
            "required": ["query"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("FIRECRAWL_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("FIRECRAWL_API_KEY", "")
    if not key:
        return {"error": "FIRECRAWL_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=FIRECRAWL_BASE, headers=_headers(), timeout=60.0) as c:
            if tool_name == "firecrawl_scrape":
                payload: dict[str, Any] = {
                    "url": arguments["url"],
                    "formats": arguments.get("formats", ["markdown"]),
                    "onlyMainContent": arguments.get("only_main_content", True),
                }
                if include := arguments.get("include_tags"):
                    payload["includeTags"] = include
                if exclude := arguments.get("exclude_tags"):
                    payload["excludeTags"] = exclude
                if wait := arguments.get("wait_for"):
                    payload["waitFor"] = wait
                if timeout := arguments.get("timeout"):
                    payload["timeout"] = timeout
                r = await c.post("/scrape", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "firecrawl_crawl":
                payload = {
                    "url": arguments["url"],
                    "maxDepth": arguments.get("max_depth", 2),
                    "limit": arguments.get("limit", 10),
                    "scrapeOptions": {
                        "formats": arguments.get("formats", ["markdown"]),
                        "onlyMainContent": arguments.get("only_main_content", True),
                    },
                    "allowExternalLinks": arguments.get("allow_external_links", False),
                }
                if excl := arguments.get("exclude_patterns"):
                    payload["excludePaths"] = excl
                if incl := arguments.get("include_patterns"):
                    payload["includePaths"] = incl
                r = await c.post("/crawl", json=payload)
                r.raise_for_status()
                crawl_data = r.json()
                # Return job ID for async crawls
                return {
                    "job_id": crawl_data.get("id"),
                    "status": crawl_data.get("status", "started"),
                    "data": crawl_data.get("data", []),
                }

            elif tool_name == "firecrawl_map":
                payload = {
                    "url": arguments["url"],
                    "limit": arguments.get("limit", 100),
                    "includeSubdomains": arguments.get("include_subdomains", False),
                }
                if search := arguments.get("search"):
                    payload["search"] = search
                r = await c.post("/map", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "firecrawl_batch_scrape":
                payload = {
                    "urls": arguments["urls"],
                    "formats": arguments.get("formats", ["markdown"]),
                    "onlyMainContent": arguments.get("only_main_content", True),
                }
                r = await c.post("/batch/scrape", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "firecrawl_search":
                payload = {
                    "query": arguments["query"],
                    "limit": arguments.get("limit", 5),
                    "lang": arguments.get("lang", "en"),
                    "country": arguments.get("country", "us"),
                }
                if scrape_opts := arguments.get("scrape_options"):
                    payload["scrapeOptions"] = scrape_opts
                r = await c.post("/search", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("firecrawl_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
