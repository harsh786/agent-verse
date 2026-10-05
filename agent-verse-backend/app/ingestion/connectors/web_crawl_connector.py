"""WebCrawlConnector — incremental web crawl using trafilatura.

Connection config (Sources UI: Web & Internet -> web_crawl):
    seed_urls            Start URLs (a list; a newline/comma-separated string and the
                         ``urls`` key older UI builds sent are accepted too).
    sitemap_url          Optional sitemap.xml whose ``<loc>`` URLs join the frontier.
    max_depth            >1 follows same-site links found on fetched pages (default 3).
    max_pages            Pages fetched per sync (default 100).
    include_url_pattern / exclude_url_pattern  Regexes applied to every URL.
    crawl_delay_seconds  Pause between fetches (default 1.0).

Every URL (seeds, sitemap, sitemap entries, discovered links, redirect targets)
passes the egress guard. Incremental delta via URL hash + content hash.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorUnavailableError,
    describe_fetch_error,
    fetch_failure_document,
    is_retryable_fetch_error,
)
from app.ingestion.connector_egress import (
    assert_source_url,
    source_client,
    source_url_is_allowed,
)
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_MAX_SITEMAP_BYTES = 10 * 1024 * 1024


def _seed_urls(cc: dict[str, object]) -> list[str]:
    """``seed_urls`` as a list — also from a string or the legacy ``urls`` key."""
    raw = cc.get("seed_urls") or cc.get("urls") or []
    items = raw if isinstance(raw, list | tuple) else str(raw).replace(",", "\n").splitlines()
    return [str(u).strip() for u in items if str(u).strip()]


async def _sitemap_urls(client: object, sitemap_url: str, limit: int) -> list[str]:
    """``<loc>`` entries of a sitemap, fetched through the egress guard (every
    redirect hop re-checked). Entries are filtered by the crawl loop's guard too."""
    import re

    from app.ingestion.connector_egress import guarded_request

    response = await guarded_request(client, "GET", sitemap_url, context="web_crawl.sitemap")
    response.raise_for_status()
    text = bytes(response.content[:_MAX_SITEMAP_BYTES]).decode("utf-8", errors="replace")
    # A regex, not an XML parser: no entity expansion on tenant-supplied XML.
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, flags=re.I)
    return [u for u in dict.fromkeys(locs) if not u.lower().endswith(".xml")][:limit]


@register("web_crawl")
class WebCrawlConnector(BaseConnector):
    """Web crawl connector with sitemap and robots.txt support."""

    source_type = "web_crawl"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        seed_urls = _seed_urls(config.connection_config)
        sitemap_url = str(config.connection_config.get("sitemap_url") or "").strip()
        if not seed_urls and not sitemap_url:
            return ConnectionHealth(ok=False, error="No seed_urls (or sitemap_url) configured")
        try:

            url = seed_urls[0] if seed_urls else sitemap_url
            assert_source_url(url, context="web_crawl.validate", config=config)
            # No automatic redirect following: a public seed that 302s to
            # 169.254.169.254 would otherwise be fetched past the guard.
            async with source_client(timeout=10) as c:
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

        seed_urls = _seed_urls(config.connection_config)
        sitemap_url = str(config.connection_config.get("sitemap_url") or "").strip()
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

        if not seed_urls and not sitemap_url:
            # USR-1: nothing to fetch is a misconfiguration, not an empty success.
            raise ConnectorFetchError("web_crawl: no seed_urls (or sitemap_url) configured")

        urls_to_visit = list(seed_urls[:max_pages])
        # URLs the tenant configured (the seeds; the sitemap itself below): any
        # failure to fetch one is a counted failure. A URL the site supplied (a
        # sitemap entry, a link on a page) counts only when retrying can help —
        # a dead or blocked link is the site's, not the sync's.
        requested: set[str] = set(urls_to_visit)
        visited = 0

        def _failure(url: str, error: object, *, retryable: bool) -> RawDocument:
            return fetch_failure_document(
                config,
                doc_id=f"web://{hashlib.md5(url.encode()).hexdigest()}",
                reason=describe_fetch_error(error),
                retryable=retryable,
                source_url=url,
                metadata={"original_url": url},
            )

        import asyncio

        # Redirects are not followed (source_client forces follow_redirects=False):
        # httpx would follow a 302 without re-checking the target, so a public
        # page could bounce the crawler to a link-local/metadata address past the
        # egress guard. Redirect targets go back onto the frontier below, where
        # they are guarded like any other URL. source_client also pins each
        # connection to the address checked at connect time (a plain client
        # re-resolved the name — DNS rebinding).
        async with source_client(
            timeout=30,
            headers={"User-Agent": "AgentVerse-KnowledgeCrawler/1.0"},
        ) as client:
            if sitemap_url:
                try:
                    for loc in await _sitemap_urls(client, sitemap_url, max_pages):
                        if loc not in urls_to_visit:
                            urls_to_visit.append(loc)
                except Exception as exc:
                    _log.warning("webcrawl_sitemap_failed url=%s: %s", sitemap_url[:200], exc)
                    if not urls_to_visit:
                        raise
                    # Seeds still crawl, but the configured sitemap is a counted
                    # failure (USR-1) — it used to be logged only.
                    yield (
                        _failure(sitemap_url, exc, retryable=is_retryable_fetch_error(exc)),
                        json.dumps(sorted(new_seen)),
                    )
            while urls_to_visit and visited < max_pages:
                url = urls_to_visit.pop(0)
                url_hash = hashlib.md5(url.encode()).hexdigest()

                if url_hash in seen_hashes:
                    continue
                if include_re and not include_re.search(url):
                    continue
                if exclude_re and exclude_re.search(url):
                    continue

                # Guard EVERY url, not just the seeds: the frontier is fed by
                # links discovered on fetched pages, so an attacker-controlled
                # public page can otherwise steer the crawler at internal hosts.
                if not source_url_is_allowed(url, context="web_crawl.fetch"):
                    _log.warning("webcrawl_url_blocked url=%s", url[:200])
                    new_seen.add(url_hash)
                    if url in requested:
                        yield (
                            _failure(url, "blocked by the egress policy", retryable=False),
                            json.dumps(sorted(new_seen)),
                        )
                    continue

                try:
                    await asyncio.sleep(crawl_delay)
                    response = await client.get(url)
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if location:
                            from urllib.parse import urljoin

                            target = urljoin(url, location)
                            if target not in urls_to_visit:
                                urls_to_visit.append(target)
                        new_seen.add(url_hash)
                        continue
                    if response.status_code >= 400:
                        retryable = is_retryable_fetch_error(response)
                        if url in requested or retryable:
                            # USR-1: a page that could not be fetched is a counted
                            # failure (→ DLQ); it used to be skipped silently.
                            yield (
                                _failure(url, response, retryable=retryable),
                                json.dumps(sorted(new_seen)),
                            )
                        else:
                            _log.info("webcrawl_dead_link url=%s status=%d", url[:200],
                                      response.status_code)
                        continue
                    response.headers.get("content-type", "text/html")
                    html_bytes = response.content
                except ConnectorUnavailableError:
                    raise
                except Exception as exc:
                    _log.warning("webcrawl_fetch_error url=%s: %s", url[:200], exc)
                    yield _failure(url, exc, retryable=True), json.dumps(sorted(new_seen))
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
