"""HTML uploads keep the content and drop the page chrome (P1a-12).

Live P1a (KB-UPLOAD-HARD[html-boilerplate]): the backend image has neither
trafilatura nor bs4 (bs4 is a dev dependency), so every HTML upload took the
regex tag-strip fallback: navigation, the cookie banner, "Related articles",
the newsletter footer and the skip link were all indexed with the article,
and table cells lost their rows. HTML is now read with lxml (a runtime
dependency): script/style/nav/header/footer/aside/forms and cookie / consent /
newsletter / sidebar blocks are dropped, <main>/<article> is preferred, and
headings, list items, code blocks and table rows keep their structure.
"""

from __future__ import annotations

from app.ingestion.parsers.html_parser import HTMLParser

_PAGE = """<!doctype html><html><head><title>Kestrel Wharf - Meridian portal</title>
<style>.banner{position:fixed}</style><script>function trackVisitor(){}</script></head>
<body><a class="skip-link" href="#main">Skip to main content</a>
<header><nav><ul><li><a href="/a">Portal section 1</a></li></ul></nav></header>
<div id="cookie-banner">We use cookies. <button>Cookie preferences</button></div>
<aside><h3>Related articles you may like</h3><ul><li>Story 1</li></ul></aside>
<main id="main"><article><header><h1>Kestrel Wharf berth booking</h1>
<p class="byline">Marine Services, 2 Oct 2026</p></header>
<p>Booking requests close <b>72 hours</b> before arrival.</p>
<h2>How to book</h2><ol><li>Sign in to the portal.</li><li>Upload the stowage plan.</li></ol>
<pre><code>berthctl book --wharf kestrel
berthctl confirm</code></pre>
<table><tr><th>Wharf</th><th>Max draft</th></tr><tr><td>Kestrel Wharf</td><td>14.2 m</td></tr>
</table><div class="share-buttons">Share on social</div></article></main>
<footer><p>Subscribe to our newsletter</p><p>All rights reserved Meridian Digital</p></footer>
</body></html>"""


def _text(html: str = _PAGE) -> str:
    return HTMLParser().parse(html)


def test_page_chrome_is_dropped() -> None:
    text = _text()
    for junk in ("Skip to main content", "Portal section", "Cookie preferences",
                 "Related articles", "Subscribe to our newsletter", "All rights reserved",
                 "trackVisitor", "position:fixed", "Share on social"):
        assert junk not in text, junk


def test_article_content_keeps_its_structure() -> None:
    lines = _text().splitlines()
    assert "# Kestrel Wharf berth booking" in lines
    assert "Marine Services, 2 Oct 2026" in lines  # the article's own header stays
    assert "Booking requests close 72 hours before arrival." in lines
    assert "## How to book" in lines
    assert "- Sign in to the portal." in lines and "- Upload the stowage plan." in lines
    assert "Wharf: Kestrel Wharf; Max draft: 14.2 m" in lines


def test_code_blocks_are_kept_verbatim() -> None:
    assert "berthctl book --wharf kestrel\nberthctl confirm" in _text()


def test_a_page_without_main_drops_nav_and_footer_from_the_body() -> None:
    html = ("<html><body><nav>Home | About</nav><h1>Tariff note</h1><p>Reefer plug-in "
            "costs INR 2,150 per day.</p><footer>Contact us</footer></body></html>")
    text = _text(html)
    assert "Reefer plug-in costs INR 2,150 per day." in text
    assert "Home | About" not in text and "Contact us" not in text


def test_rtl_and_unicode_text_is_preserved() -> None:
    html = ('<html dir="rtl"><body><main><p>يجب ارتداء خوذة زرقاء في رصيف الياسمين.</p>'
            "<p>青岚码头 640 个插座</p></main></body></html>")
    text = _text(html)
    assert "يجب ارتداء خوذة زرقاء في رصيف الياسمين." in text and "青岚码头 640 个插座" in text


def test_entities_are_decoded() -> None:
    assert "Fish & Chips <tag>" in _text("<p>Fish &amp; Chips &lt;tag&gt;</p>")


def test_title_is_used_when_the_page_has_no_h1() -> None:
    text = _text("<html><head><title>Gate hours</title></head><body><p>Gate 4 opens at 05:00."
                 "</p></body></html>")
    assert text.splitlines()[0] == "# Gate hours"
