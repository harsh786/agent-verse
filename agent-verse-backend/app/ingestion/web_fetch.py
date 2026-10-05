"""Fetching web resources for knowledge ingestion (URL ingest and the web crawl).

One fetch path for ``POST /knowledge/ingest/url``, document re-ingestion and the
``web_crawl`` connector:

* **Egress.** Every URL and every redirect hop passes the ingestion egress
  guard (:func:`app.ingestion.connector_egress.guarded_fetch`): public
  addresses, or a host on the operator's ingestion allowlist; a redirect to an
  internal address is refused and never requested; at most 5 hops; a
  permanent move (301/308) is recorded.
* **Size.** The body is streamed and refused past ``max_bytes`` (a declared
  ``Content-Length`` over the cap is refused before reading).
* **Honest failures.** :class:`WebFetchError` says what went wrong (blocked,
  redirect loop, upstream HTTP status, timeout, network, too large), whether a
  retry can help, and the HTTP status an API should answer with.
* **Charset.** :func:`decode_web_text` follows the browser order: BOM, the
  Content-Type charset, ``<meta charset>`` / ``http-equiv`` / XML declaration,
  UTF-8, then a detected legacy encoding.
* **Kind.** :func:`web_resource_ext` decides what a resource is from its bytes
  first (``%PDF``, OOXML parts, PNG/JPEG magic), then the Content-Type, then the
  URL's extension — servers mislabel files constantly.
"""

from __future__ import annotations

import codecs
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "HTML_MAX_BYTES",
    "WebFetchError",
    "WebResource",
    "decode_web_text",
    "fetch_web_resource",
    "looks_like_html",
    "web_resource_ext",
]

# HTML is parsed into a DOM (lxml): a page above this is refused, whatever the
# document limit is (a 50 MiB HTML page would need ~10x that in memory).
HTML_MAX_BYTES = 10 * 1024 * 1024

# Upstream statuses after which a retry can help.
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


# What an API answers for each kind of fetch failure.
_ERROR_HTTP_STATUS: dict[str, int] = {"blocked": 400, "redirect_loop": 422, "http": 502,
                                      "timeout": 504, "network": 502, "too_large": 413}


class WebFetchError(Exception):
    """A web resource could not be fetched; ``kind`` says why.

    ``kind``: ``blocked`` (egress policy, incl. a redirect hop), ``redirect_loop``,
    ``http`` (the server answered an error status), ``timeout``, ``network``,
    ``too_large``. ``http_status`` is what an API should answer the caller with.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        retryable: bool,
        upstream_status: int | None = None,
        url: str = "",
        moved: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.upstream_status = upstream_status
        self.url = url
        self.moved = moved
        # Seconds the server asked to wait (``Retry-After`` on a 429 / 503).
        self.retry_after = retry_after

    @property
    def http_status(self) -> int:
        return _ERROR_HTTP_STATUS.get(self.kind, 502)


@dataclass
class WebResource:
    """A fetched resource: its bytes and where they came from."""

    url: str
    final_url: str
    status: int
    content_type: str
    data: bytes
    moved: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


async def fetch_web_resource(
    client: Any,
    url: str,
    *,
    context: str,
    max_bytes: int,
    headers: dict[str, str] | None = None,
) -> WebResource:
    """GET ``url`` through the egress guard, streamed under ``max_bytes``.

    ``client`` is a :func:`~app.ingestion.connector_egress.source_client`
    (connections pinned to checked addresses). Raises :class:`WebFetchError`.
    """
    import httpx

    from app.ingestion.connector_egress import ConnectorEgressBlockedError, guarded_fetch
    from app.net.ssrf_guard import SSRFError

    try:
        fetched = await guarded_fetch(
            client, "GET", url, context=context, stream=True, headers=headers or {}
        )
    except (ConnectorEgressBlockedError, SSRFError) as exc:
        raise WebFetchError(
            f"{url}: blocked by the egress policy: {exc}", kind="blocked", retryable=False,
            url=url,
        ) from exc
    except httpx.TooManyRedirects as exc:
        raise WebFetchError(
            f"{url}: too many redirects (a redirect loop?): {exc}", kind="redirect_loop",
            retryable=False, url=url,
        ) from exc
    except httpx.TimeoutException as exc:
        raise WebFetchError(
            f"{url}: timed out ({type(exc).__name__})", kind="timeout", retryable=True, url=url
        ) from exc
    except httpx.TransportError as exc:
        raise WebFetchError(
            f"{url}: could not connect ({type(exc).__name__}: {exc})"[:300], kind="network",
            retryable=True, url=url,
        ) from exc

    response = fetched.response
    moved = fetched.move_notice()
    try:
        status = int(response.status_code)
        if status >= 300:
            snippet = ""
            try:
                body = b""
                async for part in response.aiter_bytes():
                    body += part
                    if len(body) >= 300:
                        break
                snippet = body[:200].decode("utf-8", errors="replace").strip()
            except Exception:
                snippet = ""
            reason = getattr(response, "reason_phrase", "") or ""
            raise WebFetchError(
                f"{fetched.final_url}: HTTP {status} {reason}".strip()
                + (f": {snippet}" if snippet else ""),
                kind="http",
                retryable=status >= 500 or status in _RETRYABLE_STATUSES,
                upstream_status=status,
                url=url,
                moved=moved,
                retry_after=_retry_after_seconds(response.headers.get("retry-after")),
            )
        declared = str(response.headers.get("content-length") or "").strip()
        if declared.isdigit() and int(declared) > max_bytes:
            raise _too_large(url, max_bytes, moved)
        chunks: list[bytes] = []
        total = 0
        async for part in response.aiter_bytes():
            total += len(part)
            if total > max_bytes:
                raise _too_large(url, max_bytes, moved)
            chunks.append(part)
    except WebFetchError:
        raise
    except httpx.TimeoutException as exc:
        raise WebFetchError(
            f"{url}: timed out reading the body ({type(exc).__name__})", kind="timeout",
            retryable=True, url=url, moved=moved,
        ) from exc
    except httpx.TransportError as exc:
        raise WebFetchError(
            f"{url}: the connection failed while reading ({type(exc).__name__})",
            kind="network", retryable=True, url=url, moved=moved,
        ) from exc
    finally:
        await response.aclose()
    keep = ("content-type", "etag", "last-modified", "retry-after", "x-robots-tag")
    return WebResource(
        url=url,
        final_url=fetched.final_url,
        status=status,
        content_type=str(response.headers.get("content-type") or ""),
        data=b"".join(chunks),
        moved=moved,
        headers={k: str(response.headers[k]) for k in keep if k in response.headers},
    )


def _retry_after_seconds(value: object) -> float | None:
    """``Retry-After`` as seconds (delta-seconds or an HTTP date), or None."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    from datetime import UTC, datetime
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _too_large(url: str, max_bytes: int, moved: dict[str, Any] | None) -> WebFetchError:
    return WebFetchError(
        f"{url}: larger than the {_human(max_bytes)} limit; not fetched",
        kind="too_large", retryable=False, url=url, moved=moved,
    )


def _human(n: int) -> str:
    return f"{n // (1024 * 1024)} MiB" if n >= 1024 * 1024 else f"{n} bytes"


# ── charset ──────────────────────────────────────────────────────────────────

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.I)
_XML_ENCODING = re.compile(rb"""^\s*<\?xml[^>]+encoding\s*=\s*["']([A-Za-z0-9._:-]+)""", re.I)
_HEADER_CHARSET = re.compile(r"""charset\s*=\s*["']?([A-Za-z0-9._:-]+)""", re.I)
# WHATWG: these labels mean windows-1252 in a browser (and pages rely on it).
_AS_CP1252 = frozenset({"latin1", "iso8859-1", "ascii", "cp819", "iso-8859-1", "us-ascii"})


_LATIN_GUESSES = frozenset({
    "cp1250", "cp1252", "cp1254", "cp1257", "cp1258", "iso8859-2", "iso8859-3", "iso8859-4",
    "iso8859-9", "iso8859-10", "iso8859-13", "iso8859-14", "iso8859-15", "iso8859-16",
    "mac-roman", "mac-latin2", "mac-iceland", "mac-turkish", "cp437", "cp850", "cp852",
    "cp858", "cp1125", "latin-1", "hp-roman8",
})


def _codec(label: str) -> str | None:
    label = label.strip().lower()
    if not label:
        return None
    try:
        name = codecs.lookup(label).name
    except LookupError:
        return None
    if label in _AS_CP1252 or name in {"latin-1", "iso8859-1", "ascii"}:
        return "cp1252"
    return name


def decode_web_text(data: bytes, content_type: str = "") -> str:
    """The text of a web resource, decoded the way a browser would."""
    for bom, enc in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"),
                     (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF16_LE, "utf-16"),
                     (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, errors="replace")
    declared: list[str | None] = []
    match = _HEADER_CHARSET.search(content_type or "")
    if match:
        declared.append(_codec(match.group(1)))
    head = data[:4096]
    for pattern in (_XML_ENCODING, _META_CHARSET):
        found = pattern.search(head)
        if found:
            declared.append(_codec(found.group(1).decode("ascii", errors="ignore")))
    for enc in declared:
        if enc:
            return data.decode(enc, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data[:200_000]).best()
        enc = _codec(best.encoding) if best is not None and best.encoding else None
        # Short Western text is easily "detected" as cp1250 / latin-x; a Latin
        # single-byte guess is read as windows-1252 (what browsers default to),
        # while a confident CJK / Cyrillic / Arabic / Greek … guess is used.
        if enc and enc not in _LATIN_GUESSES:
            return data.decode(enc, errors="replace")
    except ImportError:  # pragma: no cover - charset-normalizer ships with requests
        pass
    return data.decode("cp1252", errors="replace")


# ── kind ─────────────────────────────────────────────────────────────────────

_MIME_EXT = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
    "application/json": "json",
    "application/x-ndjson": "jsonl",
    "application/yaml": "yaml",
    "text/yaml": "yaml",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
}
_TEXT_EXTS = frozenset({"txt", "text", "md", "markdown", "csv", "tsv", "json", "jsonl",
                        "ndjson", "yaml", "yml", "html", "htm", "xhtml", "log", "rst"})
_DOC_EXTS = frozenset({"pdf", "docx", "pptx", "xlsx", "xlsm", "zip", "png", "jpg", "jpeg",
                       "webp", "doc", "xls", "ppt", "odt", "ods", "odp", "rtf", "epub"})
_HTML_START = re.compile(rb"^\s*(?:<\?xml[^>]*>\s*)?<(?:!doctype\s+html|html|head|body)[\s>]",
                         re.I)


def looks_like_html(data: bytes) -> bool:
    """A body that opens like an HTML document (servers mislabel HTML as text/plain)."""
    head = data[:1024].lstrip(b"\xef\xbb\xbf")
    return bool(_HTML_START.match(head))


def _ooxml_kind(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return "zip"
    if "[Content_Types].xml" in names:
        for prefix, ext in (("word/", "docx"), ("ppt/", "pptx"), ("xl/", "xlsx")):
            if any(n.startswith(prefix) for n in names):
                return ext
    return "zip"


def web_resource_ext(url: str, content_type: str, data: bytes) -> str:
    """The upload-style extension of a fetched resource (``pdf``, ``docx``, ``html``…)."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return _ooxml_kind(data)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    path = urlsplit(url).path
    leaf = path.rsplit("/", 1)[-1]
    url_ext = leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""
    if mime in _MIME_EXT and _MIME_EXT[mime] != "html":
        by_mime = _MIME_EXT[mime]
        if by_mime in _DOC_EXTS:
            return by_mime
    if looks_like_html(data) or mime in ("text/html", "application/xhtml+xml"):
        return "html"
    if url_ext in ("htm", "xhtml"):
        return "html"
    if url_ext in _TEXT_EXTS:
        return {"markdown": "md", "text": "txt", "yml": "yaml", "ndjson": "jsonl"}.get(
            url_ext, url_ext
        )
    if mime in _MIME_EXT:
        return _MIME_EXT[mime]
    if url_ext in _DOC_EXTS:
        return url_ext
    return "txt"
