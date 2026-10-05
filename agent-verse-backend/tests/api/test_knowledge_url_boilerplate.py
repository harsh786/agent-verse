"""P2-5 (KB-REEMBED): URL documents are indexed without page boilerplate.

Live P0: ``https://peps.python.org/pep-0020/`` was ingested as one 2,049-char
chunk whose first 809 chars were navigation and inline JavaScript (the URL
fetch stripped tags with a regex, keeping script/style *bodies*). The diluted
chunk then fell out of the top results after re-embedding. URL ingestion now
reuses the upload HTML extractor (P1a-12): script/style/nav/header/footer and
consent/sidebar chrome are dropped and ``<main>``/``<article>`` is preferred.
Re-ingestion of a URL document goes through the same fetch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

import app.net.ssrf_guard as g


def _request() -> Any:
    """URL extraction needs the app state only for OCR (images / scanned PDFs)."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

_ZEN = (
    "Beautiful is better than ugly. Explicit is better than implicit. "
    "Simple is better than complex. Complex is better than complicated."
)
_PAGE = f"""<!doctype html><html><head><title>PEP 20 - The Zen of Python</title>
<style>.pep-zero-table td {{ padding: 4px }} body {{ font-family: sans-serif }}</style>
<script>window.dataLayer = window.dataLayer || []; function gtag(){{dataLayer.push(arguments);}}
document.addEventListener('DOMContentLoaded', function () {{ initThemeToggle(); }});</script>
</head><body>
<a class="skip-link" href="#main">Skip to main content</a>
<header><nav><ul><li><a href="/">Python Enhancement Proposals</a></li>
<li><a href="/pep-0000/">PEP Index</a></li></ul></nav></header>
<aside id="sidebar"><h2>Contents</h2><ul><li>Abstract</li><li>Easter Egg</li></ul></aside>
<main id="main"><article><h1>PEP 20 - The Zen of Python</h1>
<section><h2>The Zen of Python</h2><pre>{_ZEN}</pre></section></article></main>
<footer><p>Page Source (GitHub)</p><script>trackFooter();</script></footer>
</body></html>"""


@pytest.mark.asyncio
async def test_web_url_content_drops_scripts_styles_and_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.knowledge import _fetch_url_document

    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    with respx.mock:
        respx.get("https://peps.example/pep-0020/").mock(
            return_value=httpx.Response(
                200, text=_PAGE, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
        doc = await _fetch_url_document(_request(), "https://peps.example/pep-0020/", "web")
    content = doc.text

    assert _ZEN in content
    for boilerplate in (
        "dataLayer",
        "gtag",
        "initThemeToggle",
        "trackFooter",
        "font-family",
        "Skip to main content",
        "PEP Index",
        "Page Source",
        "Easter Egg",
    ):
        assert boilerplate not in content, boilerplate
    # The article is (nearly) all that is left, so its embedding is not diluted.
    assert len(content) < len(_ZEN) + 200
    assert doc.title == "PEP 20 - The Zen of Python"


@pytest.mark.asyncio
async def test_plain_text_urls_are_kept_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.knowledge import _fetch_url_document

    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    body = "line one: a < b and c > d\nline two"
    with respx.mock:
        respx.get("https://site.example/notes.txt").mock(
            return_value=httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        )
        doc = await _fetch_url_document(_request(), "https://site.example/notes.txt", "web")
    assert doc.text == body
    assert doc.title == "notes.txt"
