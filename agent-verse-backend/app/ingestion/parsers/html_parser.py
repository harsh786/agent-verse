"""HTML parser using trafilatura for clean content extraction."""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


class HTMLParser:
    """Extract clean article text from HTML.

    Primary: :func:`extract_html_text` (lxml, a runtime dependency): page chrome
    removed, ``<main>`` / ``<article>`` preferred, headings / lists / code /
    table rows kept. The backend image ships neither trafilatura nor bs4, so
    every upload used to take the regex tag-strip fallback below and indexed
    navigation, cookie banners and footers with the article (P1a-12). The
    trafilatura -> bs4 -> regex chain remains for hosts without lxml.
    """

    def parse(self, content: str, *, url: str = "") -> str:
        try:
            structured = extract_html_text(content)
        except ImportError:
            structured = None
        except Exception as exc:  # malformed beyond lxml's recovery
            _log.debug("lxml_html_error: %s", exc)
            structured = None
        if structured:
            return structured
        # Try trafilatura next (best quality without lxml)
        try:
            import trafilatura  # type: ignore[import-not-found]

            text = trafilatura.extract(
                content,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_recall=True,
            )
            if text and len(text.strip()) > 50:
                return text.strip()
        except ImportError:
            _log.debug("trafilatura not installed")
        except Exception as exc:
            _log.debug("trafilatura_error: %s", exc)

        # Try BeautifulSoup fallback
        try:
            from bs4 import BeautifulSoup  # type: ignore[import-not-found]

            soup = BeautifulSoup(content, "html.parser")
            # Remove script/style tags
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if text:
                return text
        except ImportError:
            pass
        except Exception as exc:
            _log.debug("bs4_error: %s", exc)

        # Ultimate fallback: regex tag stripping. Script/style *content* (JS/CSS
        # source, not just the tags) must be dropped first — a plain tag-strip
        # would otherwise leak raw JS/CSS text into the extracted document.
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


def html_to_text(content: str) -> str:
    """Readable text of an HTML page or fragment, the way uploads read it (P1a-12).

    Every ingestion path that receives HTML (connector bodies, e-mail parts,
    fetched pages) uses this instead of a regex tag-strip, which kept
    ``<script>`` / ``<style>`` bodies and page chrome and flattened headings,
    lists and table rows into one line (P1d-10).
    """
    return HTMLParser().parse(content).strip()


def html_page_links(html: str, base_url: str) -> tuple[list[str], str | None, set[str], str]:
    """``(hrefs, canonical href, meta-robots directives, title)`` of a page (lxml).

    ``hrefs`` are absolute (against ``<base href>`` / ``base_url``), in page
    order, not yet filtered or normalised; directives come from
    ``<meta name="robots">`` and ``<meta name="agentverse-knowledgecrawler">``.
    """
    from urllib.parse import urljoin

    import lxml.html

    doc = lxml.html.document_fromstring(_XML_DECLARATION.sub("", html, count=1))
    base = base_url
    for href in doc.xpath("//base/@href")[:1]:
        base = urljoin(base_url, str(href).strip())
    canonical = None
    for href in doc.xpath(
        "//link[contains(concat(' ', normalize-space(@rel), ' '), ' canonical ')]/@href"
    )[:1]:
        canonical = urljoin(base, str(href).strip())
    directives: set[str] = set()
    for content in doc.xpath(
        "//meta[translate(@name,'ROBTS','robts')='robots' or translate(@name,"
        "'AGENTVRSKWLC','agentvrskwlc')='agentverse-knowledgecrawler']/@content"
    ):
        directives.update(v.strip().lower() for v in str(content).split(",") if v.strip())
    title = _norm(str(doc.findtext(".//title") or ""))
    hrefs: list[str] = []
    for href in doc.xpath("//a/@href | //area/@href"):
        value = str(href).strip()
        if not value:
            continue
        if value.lower().startswith(("mailto:", "javascript:", "tel:", "data:")):
            hrefs.append(value)
            continue
        try:
            hrefs.append(urljoin(base, value))
        except ValueError:
            continue
    return hrefs, canonical, directives, title


# ── lxml extraction ────────────────────────────────────────────────────────────

# Elements that are never content.
_DROP_TAGS = (
    "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object",
    "embed", "form", "button", "select", "input", "textarea", "dialog", "nav", "aside",
)
# Page-level chrome: dropped unless inside the article / main content.
_CHROME_TAGS = ("header", "footer")
_CHROME_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search", "dialog",
     "alertdialog", "menu", "menubar"}
)
_CHROME_WORDS = re.compile(
    r"(?:^|[\s_-])(cookies?|consent|gdpr|newsletter|subscribe|signup|share|sharing|social|"
    r"breadcrumbs?|sidebar|related|recommended|advert|ads|promo|popup|modal|skip|"
    r"skip-link|navbar|menu|masthead)(?:$|[\s_-])",
    re.IGNORECASE,
)
_HEADINGS = {f"h{i}": i for i in range(1, 7)}
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "main", "header", "footer", "blockquote", "figure",
     "figcaption", "address", "dl", "dt", "dd", "ul", "ol", "li", "pre", "table", "hr",
     "br", "details", "summary", *(_HEADINGS)}
)


_XML_DECLARATION = re.compile(r"^\ufeff?\s*<\?xml[^>]*\?>", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _is_chrome(el: Any) -> bool:
    role = (el.get("role") or "").strip().lower()
    if role in _CHROME_ROLES:
        return True
    marker = f"{el.get('id') or ''} {el.get('class') or ''}"
    return bool(marker.strip()) and bool(_CHROME_WORDS.search(marker))


def extract_html_text(content: str) -> str | None:
    """Readable text of an HTML page, or None when it has none (lxml required)."""
    import lxml.html

    if not content.strip():
        return None
    # lxml refuses a str that still carries an encoding declaration (XHTML
    # pages): the text is already decoded, so the declaration is dropped.
    content = _XML_DECLARATION.sub("", content, count=1)
    doc = lxml.html.document_fromstring(content)
    title = _norm(doc.findtext(".//title") or "")
    for el in doc.xpath("|".join(f"//{t}" for t in _DROP_TAGS)):
        el.drop_tree()
    candidates = doc.xpath("//main | //article | //*[@role='main']")
    if candidates:
        root = max(candidates, key=lambda e: len(e.text_content()))
        # nested <article>s inside the chosen <main> stay; the outer one wins
        while root.getparent() is not None and root.getparent() in candidates:
            root = root.getparent()
    else:
        body = doc.find("body")
        root = body if body is not None else doc
        for el in root.xpath(".//header | .//footer"):
            el.drop_tree()
    for el in list(root.iter()):
        if el is root or not isinstance(el.tag, str) or el.getparent() is None:
            continue
        if _is_chrome(el):
            el.drop_tree()
    lines: list[str] = []
    try:
        _html_blocks(root, lines, depth=0)
    except RecursionError:  # absurdly deep DOM: plain text, still never markup
        lines = [_norm(root.text_content())]
    lines = [line for line in lines if line.strip()]
    if title and not any(line.startswith("# ") for line in lines):
        lines.insert(0, f"# {title}")
    text = "\n".join(lines).strip()
    return text or None


def _html_blocks(el: Any, out: list[str], *, depth: int) -> None:
    """Append the block lines of ``el``'s children (inline runs joined)."""
    buf: list[str] = [el.text or ""]

    def flush() -> None:
        text = _norm(" ".join(buf))
        buf.clear()
        if text:
            out.append(text)

    for child in el:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag in _BLOCK_TAGS:
            flush()
            _html_block(child, out, depth=depth)
        elif tag:
            buf.append(child.text_content())
        buf.append(child.tail or "")
    flush()


def _html_block(el: Any, out: list[str], *, depth: int) -> None:
    tag = el.tag
    if tag in _HEADINGS:
        text = _norm(el.text_content())
        if text:
            out.append(f"{'#' * _HEADINGS[tag]} {text}")
    elif tag == "pre":
        text = el.text_content().strip("\n")
        if text.strip():
            out.append(text)
    elif tag == "table":
        out.extend(_html_table_rows(el))
    elif tag in ("ul", "ol"):
        for item in el.iterchildren("li"):
            _html_list_item(item, out, depth=depth)
    elif tag == "li":
        _html_list_item(el, out, depth=depth)
    elif tag in ("br", "hr"):
        return
    else:
        _html_blocks(el, out, depth=depth)


def _html_list_item(li: Any, out: list[str], *, depth: int) -> None:
    own: list[str] = []
    nested: list[Any] = []
    own.append(li.text or "")
    for child in li:
        if isinstance(child.tag, str) and child.tag in ("ul", "ol"):
            nested.append(child)
        elif isinstance(child.tag, str):
            own.append(child.text_content())
        own.append(child.tail or "")
    text = _norm(" ".join(own))
    if text:
        out.append(f"{'  ' * depth}- {text}")
    for sub in nested:
        for item in sub.iterchildren("li"):
            _html_list_item(item, out, depth=depth + 1)


def _html_table_rows(table: Any) -> list[str]:
    """Rows as ``header: value`` pairs (header = the first row), like DOCX tables."""
    rows: list[list[str]] = []
    for tr in table.iter("tr"):
        cells = [_norm(c.text_content()) for c in tr if isinstance(c.tag, str)
                 and c.tag in ("td", "th")]
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    header, *data = rows
    if not data:
        return [" | ".join(c for c in header if c)]
    out: list[str] = []
    for row in data:
        pairs = [
            f"{header[i] if i < len(header) and header[i] else f'col{i + 1}'}: {v}"
            for i, v in enumerate(row)
            if v
        ]
        if pairs:
            out.append("; ".join(pairs))
    return out
