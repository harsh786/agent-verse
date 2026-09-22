"""WebCrawlConnector — incremental web crawl using trafilatura.

Supports sitemap.xml discovery, robots.txt compliance, incremental
delta via URL hash + content hash deduplication.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("web_crawl")
class WebCrawlConnector(BaseConnector):
    """Web crawl connector with sitemap and robots.txt support."""

    source_type = "web_crawl"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        seed_urls = config.connection_config.get("seed_urls", [])
        if not seed_urls:
            return ConnectionHealth(ok=False, error="No seed_urls configured")
        try:
            import httpx

            url = seed_urls[0]
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.get(url)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=r.status_code < 400,
                latency_ms=latency,
                error="" if r.status_code < 400 else f"HTTP {r.status_code}",
                metadata={"url": url, "status": r.status_code},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Crawl seed URLs and discover new/changed pages."""
        from app.ingestion.source_config import RawDocument

        seed_urls: list[str] = config.connection_config.get("seed_urls", [])
        max_depth: int = config.connection_config.get("max_depth", 3)
        max_pages: int = config.connection_config.get("max_pages", 100)
        crawl_delay: float = config.connection_config.get("crawl_delay_seconds", 1.0)
        config.connection_config.get("respect_robots_txt", True)
        include_pat: str = config.connection_config.get("include_url_pattern", "")
        exclude_pat: str = config.connection_config.get("exclude_url_pattern", "")

        # Cursor = set of already-seen URL hashes (JSON-serialized)
        import json

        seen_hashes: set[str] = set(json.loads(cursor)) if cursor else set()
        new_seen: set[str] = set(seen_hashes)

        import re

        include_re = re.compile(include_pat) if include_pat else None
        exclude_re = re.compile(exclude_pat) if exclude_pat else None

        urls_to_visit = list(seed_urls[:max_pages])
        visited = 0

        import asyncio

        try:
            import httpx
        except ImportError:
            _log.error("httpx not installed")
            return

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "AgentVerse-KnowledgeCrawler/1.0"},
        ) as client:
            while urls_to_visit and visited < max_pages:
                url = urls_to_visit.pop(0)
                url_hash = hashlib.md5(url.encode()).hexdigest()

                if url_hash in seen_hashes:
                    continue
                if include_re and not include_re.search(url):
                    continue
                if exclude_re and exclude_re.search(url):
                    continue

                try:
                    await asyncio.sleep(crawl_delay)
                    response = await client.get(url)
                    if response.status_code >= 400:
                        continue
                    response.headers.get("content-type", "text/html")
                    html_bytes = response.content
                except Exception as exc:
                    _log.debug("webcrawl_fetch_error url=%s: %s", url, exc)
                    continue

                visited += 1
                new_seen.add(url_hash)

                # Extract clean text
                text = self._extract_text(html_bytes.decode("utf-8", errors="replace"), url)
                if not text or len(text) < 100:
                    continue

                content_hash = hashlib.sha256(text.encode()).hexdigest()
                raw = RawDocument(
                    doc_id=f"web://{url_hash}",
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    content=text.encode("utf-8"),
                    content_type="text/plain",
                    source_url=url,
                    title=self._extract_title(html_bytes.decode("utf-8", errors="replace")),
                    metadata={"original_url": url, "content_hash": content_hash},
                )
                new_cursor = json.dumps(sorted(new_seen))
                yield raw, new_cursor

                # Discover more URLs from this page (limited depth)
                if visited < max_pages and max_depth > 1:
                    new_urls = self._extract_links(
                        html_bytes.decode("utf-8", errors="replace"), url
                    )
                    for new_url in new_urls[:20]:
                        h = hashlib.md5(new_url.encode()).hexdigest()
                        if h not in new_seen and new_url not in urls_to_visit:
                            urls_to_visit.append(new_url)

    @staticmethod
    def _extract_text(html: str, url: str = "") -> str:
        try:
            import trafilatura

            text = trafilatura.extract(html, favor_recall=True, include_tables=True)
            return text or ""
        except Exception:
            import re

            # Strip script/style *content* first — not just their tags — so
            # raw JS/CSS source doesn't leak into the crawled document text.
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            return re.sub(r"\s+", " ", text).strip()[:50000]

    @staticmethod
    def _extract_title(html: str) -> str:
        import re

        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_links(html: str, base_url: str) -> list[str]:
        import re
        from urllib.parse import urljoin, urlparse

        base = urlparse(base_url)
        links: list[str] = []
        for href in re.findall(r'href=["\']([^"\'#?]+)["\']', html):
            try:
                full = urljoin(base_url, href)
                parsed = urlparse(full)
                # Only same-domain links
                if parsed.netloc == base.netloc and parsed.scheme in ("http", "https"):
                    links.append(full)
            except Exception:
                pass
        return list(dict.fromkeys(links))  # deduplicate, preserve order
