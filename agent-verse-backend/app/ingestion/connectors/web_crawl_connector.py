"""WebCrawlConnector — a polite, bounded, incremental crawl of one web site.

Connection config (Sources UI: Web & Internet -> web_crawl):
    seed_urls            Start URLs (a list; a newline/comma-separated string and the
                         ``urls`` key older UI builds sent are accepted too).
    sitemap_url          Optional sitemap.xml (or sitemap index) whose ``<loc>`` URLs
                         join the frontier.
    max_depth            Link depth: the seeds (and sitemap entries) are depth 1, a link
                         on a depth-d page is depth d+1; nothing deeper than this is
                         fetched (default 3; 1 = only the listed pages).
    max_pages            Pages read per sync (default 100, at most 5000); dead links do
                         not count, but all page requests stay under 3 x max_pages.
    include_url_pattern / exclude_url_pattern  Regexes applied to every URL.
    crawl_delay_seconds  Minimum pause between two requests to one host (default 1.0);
                         a larger robots.txt ``Crawl-delay`` wins (capped at 30 s).
    respect_robots_txt   Honour robots.txt (default true).
    max_page_bytes       A page larger than this is refused (default 5 MiB).

Every sync crawls the site again from the seeds (P1d-6). The previous crawler
skipped every URL it had seen in an earlier sync, so a changed page was never
read again. Unchanged pages cost a fetch but are skipped by the pipeline's
content hash. Only links on the seeds' hosts are followed. URLs are normalised
(fragment, default port, tracking / session parameters dropped, query sorted),
so ``?utm_source=…`` variants and ``#section`` links are one page; a page's
``<link rel="canonical">`` names its document, and a page whose text equals one
already read in this crawl is a duplicate, not a second document. robots.txt is
honoured (``Disallow``, ``Crawl-delay``), as are ``<meta name="robots">``
``noindex`` / ``nofollow``; 429 / 503 ``Retry-After`` is waited out once.

A complete crawl (frontier exhausted, no retryable failure, not cut by
``max_pages``) records the documents it found live; the deletion reconciler
(``POST /sources/{id}/reconcile``) removes the pages that disappeared (404/410,
unlinked, newly disallowed). An incomplete crawl never deletes anything.

Every URL (seeds, robots.txt, sitemaps, discovered links, every redirect hop)
passes the egress guard; connections are pinned to the checked address.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorUnavailableError,
    LiveListingUnavailableError,
    fetch_failure_document,
)
from app.ingestion.connector_egress import (
    assert_source_url,
    source_client,
    source_url_is_allowed,
)
from app.ingestion.connector_registry import register
from app.ingestion.source_config import CONNECTOR_MOVED_KEY
from app.ingestion.web_fetch import (
    HTML_MAX_BYTES,
    WebFetchError,
    WebResource,
    decode_web_text,
    fetch_web_resource,
    web_resource_ext,
)

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

USER_AGENT = "AgentVerse-KnowledgeCrawler/1.0"
_ROBOTS_AGENT = "AgentVerse-KnowledgeCrawler"
_MAX_SITEMAP_BYTES = 10 * 1024 * 1024
_MAX_SITEMAPS = 10
_MAX_PAGES_CAP = 5000
_DEFAULT_PAGE_BYTES = 5 * 1024 * 1024
_MAX_CRAWL_DELAY = 30.0
_MAX_RETRY_AFTER = 30.0
_MAX_LINKS_PER_PAGE = 500
# Live-set size recorded in the cursor for the deletion reconciler.
_MAX_LIVE_IDS = _MAX_PAGES_CAP

# Query parameters that never change a page: analytics / click ids / sessions.
_DROP_PARAMS = re.compile(
    r"^(utm_[a-z]+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|_ga|_gl|yclid|igshid|ref|"
    r"ref_src|source|sessionid|jsessionid|phpsessid|sid|session_id)$",
    re.IGNORECASE,
)
# Documents linked from pages that are read like an upload of the file.
_DOCUMENT_MIMES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_TEXT_EXTS = frozenset({"txt", "md", "csv", "tsv", "json", "jsonl", "yaml", "rst", "log"})

# CONNECTOR_REPLAY_KEY "kind" of a page the crawl could not fetch (see replay_event).
_REPLAY_KIND = "web_page"


def normalize_url(url: str, base: str = "") -> str | None:
    """The canonical form of a crawlable URL, or None (not http(s), unparsable)."""
    try:
        full = urljoin(base, url.strip()) if base else url.strip()
        parts = urlsplit(full)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]" + (f":{port}" if port not in (None, 80, 443) else "")
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
               if not _DROP_PARAMS.match(k))
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def page_doc_id(url: str) -> str:
    """Document id of a crawled page (its normalised URL)."""
    return f"web://{hashlib.md5(url.encode()).hexdigest()}"


def _seed_urls(cc: dict[str, object]) -> list[str]:
    """``seed_urls`` as a list — also from a string or the legacy ``urls`` key."""
    raw = cc.get("seed_urls") or cc.get("urls") or []
    items = raw if isinstance(raw, list | tuple) else str(raw).replace(",", "\n").splitlines()
    return [str(u).strip() for u in items if str(u).strip()]


def _int(cc: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(cc.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _float(cc: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(cc.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


@dataclass
class _Settings:
    seeds: list[str]
    sitemap_url: str
    max_depth: int
    max_pages: int
    delay: float
    robots: bool
    max_page_bytes: int
    include: re.Pattern[str] | None
    exclude: re.Pattern[str] | None

    @classmethod
    def of(cls, config: SourceConfig) -> _Settings:
        from app.core.config import get_settings

        cc = dict(config.connection_config or {})
        doc_cap = min(int(get_settings().knowledge_max_upload_bytes),
                      int(config.max_doc_size_bytes or _DEFAULT_PAGE_BYTES * 10))
        include = str(cc.get("include_url_pattern") or "")
        exclude = str(cc.get("exclude_url_pattern") or "")
        return cls(
            seeds=_seed_urls(cc),
            sitemap_url=str(cc.get("sitemap_url") or "").strip(),
            max_depth=_int(cc, "max_depth", 3, 1, 20),
            max_pages=_int(cc, "max_pages", 100, 1, _MAX_PAGES_CAP),
            delay=_float(cc, "crawl_delay_seconds", 1.0, 0.0, 60.0),
            robots=str(cc.get("respect_robots_txt", True)).lower() not in ("false", "0", "no"),
            max_page_bytes=_int(cc, "max_page_bytes", _DEFAULT_PAGE_BYTES, 1024, doc_cap),
            include=re.compile(include) if include else None,
            exclude=re.compile(exclude) if exclude else None,
        )


@dataclass
class _Page:
    """What one fetched URL yielded."""

    url: str
    doc: RawDocument | None = None
    links: list[str] = field(default_factory=list)
    canonical: str | None = None
    text_hash: str = ""
    noindex: bool = False


class _Robots:
    """robots.txt per host (fetched once per crawl, through the egress guard)."""

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}

    def known(self, origin: str) -> bool:
        return origin in self._parsers

    def set(self, origin: str, parser: Any) -> None:
        self._parsers[origin] = parser

    def allowed(self, url: str) -> bool:
        parser = self._parsers.get(_origin(url))
        return True if parser is None else bool(parser.can_fetch(_ROBOTS_AGENT, url))

    def delay(self, url: str) -> float:
        parser = self._parsers.get(_origin(url))
        if parser is None:
            return 0.0
        value = parser.crawl_delay(_ROBOTS_AGENT)
        return min(float(value), _MAX_CRAWL_DELAY) if value else 0.0


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _deny_all() -> Any:
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    return parser


def _page_failure(config: SourceConfig, url: str, error: object, *,
                  retryable: bool) -> RawDocument:
    """A counted failure for a page that could not be fetched (USR-1); a retryable
    one carries a replay reference so the DLQ retry fetches it again (USR-4)."""
    return fetch_failure_document(
        config,
        doc_id=page_doc_id(url),
        reason=str(error)[:500],
        retryable=retryable,
        source_url=url,
        replay={"kind": _REPLAY_KIND, "url": url} if retryable else None,
        metadata={"original_url": url},
    )


def _html_parts(html: str, base_url: str) -> tuple[list[str], str | None, set[str], str]:
    """(links, canonical, meta-robots directives, title) of a page, links normalised."""
    from app.ingestion.parsers.html_parser import html_page_links

    try:
        hrefs, canonical, directives, title = html_page_links(html, base_url)
    except Exception:  # unparsable markup: no links, nothing else known
        return [], None, set(), ""
    links: list[str] = []
    for href in hrefs:
        if href.lower().startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue
        normalized = normalize_url(href, base_url)
        if normalized:
            links.append(normalized)
        if len(links) >= _MAX_LINKS_PER_PAGE * 2:
            break
    canonical_url = normalize_url(canonical, base_url) if canonical else None
    return list(dict.fromkeys(links)), canonical_url, directives, title


async def _sitemap_urls(crawler: _Crawl, sitemap_url: str, limit: int) -> list[str]:
    """``<loc>`` entries of a sitemap (one level of sitemap index followed)."""
    found: list[str] = []
    queue = [sitemap_url]
    fetched = 0
    while queue and fetched < _MAX_SITEMAPS and len(found) < limit:
        url = queue.pop(0)
        fetched += 1
        res = await crawler.fetch(url, max_bytes=_MAX_SITEMAP_BYTES)
        text = res.data.decode("utf-8", errors="replace")
        # A regex, not an XML parser: no entity expansion on tenant-supplied XML.
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, flags=re.I)
        is_index = bool(re.search(r"<sitemapindex[\s>]", text, flags=re.I))
        for loc in locs:
            loc = loc.replace("&amp;", "&")
            if is_index or loc.lower().endswith((".xml", ".xml.gz")):
                if url == sitemap_url and len(queue) < _MAX_SITEMAPS:
                    queue.append(loc)
                continue
            normalized = normalize_url(loc)
            if normalized:
                found.append(normalized)
    return list(dict.fromkeys(found))[:limit]


class _Crawl:
    """One crawl's HTTP side: politeness per host, robots.txt, Retry-After."""

    def __init__(self, client: Any, settings: _Settings) -> None:
        self.client = client
        self.settings = settings
        self.robots = _Robots()
        self._last: dict[str, float] = {}
        self.requests = 0

    async def _wait_turn(self, url: str) -> None:
        origin = _origin(url)
        gap = max(self.settings.delay, self.robots.delay(url))
        last = self._last.get(origin)
        if last is not None and gap > 0:
            wait = last + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        self._last[origin] = time.monotonic()

    async def fetch(self, url: str, *, max_bytes: int) -> WebResource:
        """GET ``url`` politely; 429 / 503 with Retry-After is waited out once."""
        for attempt in (1, 2):
            await self._wait_turn(url)
            self.requests += 1
            try:
                return await fetch_web_resource(
                    self.client, url, context="web_crawl.fetch", max_bytes=max_bytes,
                    headers={"User-Agent": USER_AGENT},
                )
            except WebFetchError as exc:
                retry_after = _retry_after(exc)
                if attempt == 1 and retry_after is not None:
                    await asyncio.sleep(retry_after)
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def load_robots(self, url: str) -> None:
        origin = _origin(url)
        if not self.settings.robots or self.robots.known(origin):
            return
        from urllib.robotparser import RobotFileParser

        try:
            res = await self.fetch(f"{origin}/robots.txt", max_bytes=512 * 1024)
        except WebFetchError as exc:
            if exc.kind == "http" and exc.upstream_status is not None and \
                    400 <= exc.upstream_status < 500:
                self.robots.set(origin, None)  # no robots.txt: everything allowed
                return
            # RFC 9309: an unreachable robots.txt (5xx, timeout) means "assume
            # complete disallow" until it can be read.
            _log.warning("webcrawl_robots_unavailable origin=%s: %s", origin, exc)
            self.robots.set(origin, _deny_all())
            raise ConnectorFetchError(f"robots.txt of {origin} could not be read: {exc}") from exc
        parser = RobotFileParser()
        parser.parse(decode_web_text(res.data, res.content_type).splitlines())
        self.robots.set(origin, parser)


def _retry_after(exc: WebFetchError) -> float | None:
    if exc.kind != "http" or exc.upstream_status not in (429, 503):
        return None
    if exc.retry_after is None:
        return None
    return min(exc.retry_after, _MAX_RETRY_AFTER)


@register("web_crawl")
class WebCrawlConnector(BaseConnector):
    """Web crawl connector with sitemap, robots.txt and incremental re-crawl."""

    source_type = "web_crawl"
    supports_deletion_tracking = True  # iter_live_doc_ids -> reconcile (P1d-9)

    def __init__(self) -> None:
        super().__init__()
        self.completed_cursor: str | None = None

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        t0 = time.perf_counter()
        settings = _Settings.of(config)
        if not settings.seeds and not settings.sitemap_url:
            return ConnectionHealth(ok=False, error="No seed_urls (or sitemap_url) configured")
        url = settings.seeds[0] if settings.seeds else settings.sitemap_url
        try:
            assert_source_url(url, context="web_crawl.validate", config=config)
            async with source_client(timeout=10, headers={"User-Agent": USER_AGENT}) as c:
                res = await fetch_web_resource(
                    c, url, context="web_crawl.validate", max_bytes=settings.max_page_bytes
                )
        except WebFetchError as exc:
            return ConnectionHealth(
                ok=False, latency_ms=(time.perf_counter() - t0) * 1000, error=str(exc)[:300],
                metadata={"url": url, "status": exc.upstream_status},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc)[:300])
        return ConnectionHealth(
            ok=True, latency_ms=(time.perf_counter() - t0) * 1000,
            metadata={"url": url, "status": res.status, "final_url": res.final_url},
        )

    def manages_doc_id(self, doc_id: str) -> bool:
        return str(doc_id).startswith("web://")

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """The pages the last COMPLETE crawl found (see the module docstring).

        An incomplete last crawl (cut by ``max_pages``, a retryable failure, a
        robots.txt that could not be read) or none at all: nothing is known, and
        the reconciler deletes nothing.
        """
        try:
            state = json.loads(config.cursor_value or "")
        except ValueError:
            state = None
        if not isinstance(state, dict) or state.get("v") != 2 or not state.get("complete"):
            raise LiveListingUnavailableError(
                "web_crawl: the last crawl was not complete; nothing is reconciled"
            )
        for doc_id in state.get("live") or []:
            yield str(doc_id)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Crawl the site from the seeds; yield every page (unchanged ones are
        skipped by the pipeline's content hash) and every counted failure."""
        settings = _Settings.of(config)
        if not settings.seeds and not settings.sitemap_url:
            # USR-1: nothing to fetch is a misconfiguration, not an empty success.
            raise ConnectorFetchError("web_crawl: no seed_urls (or sitemap_url) configured")
        self.completed_cursor = None
        progress = cursor or ""

        seeds: list[str] = []
        failures: list[RawDocument] = []
        for raw in settings.seeds:
            normalized = normalize_url(raw)
            if normalized is None or not source_url_is_allowed(normalized,
                                                               context="web_crawl.fetch"):
                failures.append(_page_failure(
                    config, raw, "blocked by the egress policy (or not an http(s) URL)",
                    retryable=False,
                ))
                continue
            seeds.append(normalized)
        for failure in failures:
            yield failure, progress
        hosts = {urlsplit(u).netloc for u in seeds}
        if settings.sitemap_url:
            hosts.add(urlsplit(normalize_url(settings.sitemap_url) or "").netloc)
        requested = set(seeds)

        live: list[str] = []
        complete = True
        visited: set[str] = set()
        indexed_as: set[str] = set()  # canonical / document URLs taken this crawl
        texts: dict[str, str] = {}  # text hash -> URL that holds it
        frontier: deque[tuple[str, int]] = deque((u, 1) for u in seeds)
        fetched = 0  # pages read
        attempts = 0  # page requests, dead links included

        async with source_client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
            crawl = _Crawl(client, settings)
            if settings.sitemap_url:
                try:
                    if settings.robots:
                        await crawl.load_robots(settings.sitemap_url)
                    for loc in await _sitemap_urls(crawl, settings.sitemap_url,
                                                   settings.max_pages):
                        if urlsplit(loc).netloc in hosts:
                            frontier.append((loc, 1))
                            requested.add(loc)
                except ConnectorUnavailableError:
                    raise
                except Exception as exc:
                    complete = False
                    _log.warning("webcrawl_sitemap_failed url=%s: %s",
                                 settings.sitemap_url[:200], exc)
                    if not frontier:
                        raise ConnectorFetchError(
                            f"web_crawl: the sitemap {settings.sitemap_url} could not be "
                            f"read: {exc}") from exc
                    retryable = not isinstance(exc, WebFetchError) or exc.retryable
                    # USR-1: the configured sitemap is a counted failure.
                    yield _page_failure(config, settings.sitemap_url, exc,
                                        retryable=retryable), progress

            while frontier:
                url, depth = frontier.popleft()
                if url in visited:
                    continue
                visited.add(url)
                if settings.include and not settings.include.search(url):
                    continue
                if settings.exclude and settings.exclude.search(url):
                    continue
                if fetched >= settings.max_pages or attempts >= settings.max_pages * 3:
                    # The site has more pages than this sync reads (pages read count;
                    # dead links only against the overall request budget).
                    complete = False
                    break
                if not source_url_is_allowed(url, context="web_crawl.fetch"):
                    _log.warning("webcrawl_url_blocked url=%s", url[:200])
                    if url in requested:
                        yield _page_failure(config, url, "blocked by the egress policy",
                                            retryable=False), progress
                    continue
                try:
                    await crawl.load_robots(url)
                except ConnectorFetchError as exc:
                    complete = False
                    if url in requested:
                        yield _page_failure(config, url, exc, retryable=True), progress
                    continue
                if not crawl.robots.allowed(url):
                    _log.info("webcrawl_robots_disallowed url=%s", url[:200])
                    if url in requested:
                        yield _page_failure(config, url, "disallowed by robots.txt",
                                            retryable=False), progress
                    continue

                attempts += 1
                try:
                    page = await self._read_page(crawl, config, url, settings)
                except ConnectorUnavailableError:
                    raise
                except WebFetchError as exc:
                    if exc.retryable:
                        complete = False
                    if url in requested or exc.retryable:
                        # USR-1 / USR-4: a page that could not be fetched is a counted
                        # failure; a retryable one goes to the DLQ with a replay ref.
                        yield _page_failure(config, url, exc, retryable=exc.retryable), \
                            progress
                    else:
                        _log.info("webcrawl_dead_link url=%s: %s", url[:200], exc)
                    continue
                fetched += 1
                final = page.url
                visited.add(final)
                if page.links and depth < settings.max_depth:
                    for link in page.links[:_MAX_LINKS_PER_PAGE]:
                        if urlsplit(link).netloc in hosts and link not in visited:
                            frontier.append((link, depth + 1))
                    if len(frontier) > settings.max_pages * 20:
                        # A link trap (an endless calendar): keep the frontier bounded.
                        complete = False
                        while len(frontier) > settings.max_pages * 20:
                            frontier.pop()
                if page.doc is None:
                    continue
                key = page.canonical or final
                if key in indexed_as:
                    continue  # a canonical duplicate of a page already read
                holder = texts.get(page.text_hash)
                if holder is not None:
                    _log.info("webcrawl_duplicate url=%s same_as=%s", url[:200], holder[:200])
                    continue
                texts[page.text_hash] = key
                indexed_as.update({key, final})
                if len(live) < _MAX_LIVE_IDS:
                    live.append(page.doc.doc_id)
                yield page.doc, progress

        state = {"v": 2, "complete": complete, "pages": len(live),
                 "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "live": sorted(live) if complete else []}
        self.completed_cursor = json.dumps(state, separators=(",", ":"))

    async def _read_page(self, crawl: _Crawl, config: SourceConfig, url: str,
                         settings: _Settings) -> _Page:
        """Fetch one URL and turn it into a document (HTML page, text or file)."""
        from app.ingestion.source_config import RawDocument

        res = await crawl.fetch(url, max_bytes=settings.max_page_bytes)
        final = normalize_url(res.final_url) or res.final_url
        ext = web_resource_ext(res.final_url, res.content_type, res.data)
        meta: dict[str, Any] = {"original_url": url, "final_url": final}
        if res.moved is not None:
            meta[CONNECTOR_MOVED_KEY] = res.moved
        page = _Page(url=final)
        if ext == "html":
            if len(res.data) > HTML_MAX_BYTES:
                raise WebFetchError(f"{url}: HTML page larger than the HTML limit",
                                    kind="too_large", retryable=False, url=url)
            html = decode_web_text(res.data, res.content_type)
            links, canonical, directives, title = _html_parts(html, res.final_url)
            if "nofollow" not in directives and "none" not in directives:
                page.links = links
            if canonical and urlsplit(canonical).netloc == urlsplit(final).netloc:
                page.canonical = canonical
            if "noindex" in directives or "none" in directives:
                return page
            text = self._extract_text(html, final)
            if not text.strip():
                return page
            doc_url = page.canonical or final
            page.text_hash = hashlib.sha256(text.encode()).hexdigest()
            meta["content_hash"] = page.text_hash
            if page.canonical and page.canonical != final:
                meta["canonical_url"] = page.canonical
            page.doc = RawDocument(
                doc_id=page_doc_id(doc_url),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                content=text.encode("utf-8"),
                # Extracted text (headings as "#", list items as "-"): markdown,
                # never re-read as HTML because of a ``.html`` URL.
                content_type="text/markdown; charset=utf-8",
                source_url=doc_url,
                title=title or self._extract_title(html),
                metadata=meta,
            )
            return page
        if ext in _TEXT_EXTS:
            text = decode_web_text(res.data, res.content_type)
            content, ctype = text.encode("utf-8"), "text/plain; charset=utf-8"
        elif ext in _DOCUMENT_MIMES:
            content, ctype = res.data, _DOCUMENT_MIMES[ext]
        else:
            return page  # images, archives, binaries: not indexed by the crawl
        page.text_hash = hashlib.sha256(content).hexdigest()
        meta["content_hash"] = page.text_hash
        leaf = urlsplit(final).path.rstrip("/").rsplit("/", 1)[-1] or "document"
        meta["filename"] = leaf if leaf.lower().endswith(f".{ext}") else f"{leaf}.{ext}"
        page.doc = RawDocument(
            doc_id=page_doc_id(final),
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            content=content,
            content_type=ctype,
            source_url=final,
            title=meta["filename"],
            metadata=meta,
        )
        return page

    async def replay_event(
        self, config: SourceConfig, reference: dict[str, Any]
    ) -> AsyncIterator[RawDocument]:
        """Fetch again one page the crawl could not read (DLQ retry, USR-4).

        The page is re-checked by the egress guard and robots.txt; its links are
        not followed (the next crawl discovers them). Yields the page, or a fresh
        failure document when it still cannot be read.
        """
        url = str(reference.get("url") or "")
        if reference.get("kind") != _REPLAY_KIND or not url:
            raise ValueError(f"not a web page replay reference: {reference!r}")
        if not source_url_is_allowed(url, context="web_crawl.replay"):
            yield _page_failure(config, url, "blocked by the egress policy", retryable=False)
            return
        settings = _Settings.of(config)
        async with source_client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
            crawl = _Crawl(client, settings)
            try:
                await crawl.load_robots(url)
                if not crawl.robots.allowed(url):
                    yield _page_failure(config, url, "disallowed by robots.txt", retryable=False)
                    return
                page = await self._read_page(crawl, config, url, settings)
            except WebFetchError as exc:
                yield _page_failure(config, url, exc, retryable=exc.retryable)
                return
            except ConnectorFetchError as exc:
                yield _page_failure(config, url, exc, retryable=True)
                return
        if page.doc is not None:
            yield page.doc

    @staticmethod
    def _extract_text(html: str, url: str = "") -> str:
        """Article text of a crawled page, page chrome removed (the upload extractor)."""
        from app.ingestion.parsers.html_parser import HTMLParser

        return HTMLParser().parse(html, url=url).strip()

    @staticmethod
    def _extract_title(html: str) -> str:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_links(html: str, base_url: str) -> list[str]:
        """Same-host links of a page, normalised."""
        base_host = urlsplit(base_url).netloc.lower()
        links, _canonical, _directives, _title = _html_parts(html, base_url)
        return [link for link in links if urlsplit(link).netloc == base_host]
