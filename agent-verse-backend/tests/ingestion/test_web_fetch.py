"""P1d: fetching web resources for knowledge ingestion (URL ingest and web crawl).

* Charset: a page's text is decoded by its BOM, the Content-Type charset, a
  ``<meta charset>`` / ``http-equiv`` / XML declaration, then UTF-8, then a
  detected legacy encoding — it used to be UTF-8 with replacement characters
  (a windows-1252 or Shift_JIS page became mojibake).
* Kind: what a fetched resource is (pdf / docx / pptx / xlsx / zip / html /
  text …) comes from its bytes first, then the Content-Type, then the URL's
  extension — a PDF served by URL used to be decoded as text.
* Size: the body is streamed and refused past the cap instead of being read
  whole into memory.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.ingestion.web_fetch import (
    WebFetchError,
    decode_web_text,
    fetch_web_resource,
    web_resource_ext,
)

# ── charset ─────────────────────────────────────────────────────────────────


def test_header_charset_wins() -> None:
    data = "Prix: 12 € — café".encode("cp1252")
    assert decode_web_text(data, "text/html; charset=windows-1252") == "Prix: 12 € — café"


def test_meta_charset_is_honoured_without_a_header_charset() -> None:
    html = '<html><head><meta charset="windows-1252"><title>t</title></head>' \
        "<body><p>Smörgåsbord für 12 € – naïve café</p></body></html>"
    text = decode_web_text(html.encode("cp1252"), "text/html")
    assert "Smörgåsbord für 12 € – naïve café" in text


def test_http_equiv_charset_shift_jis() -> None:
    html = ('<html><head><meta http-equiv="Content-Type" content="text/html; '
            'charset=Shift_JIS"></head><body><p>東京都の倉庫は午前九時に開きます</p></body></html>')
    text = decode_web_text(html.encode("shift_jis"), "text/html")
    assert "東京都の倉庫は午前九時に開きます" in text


def test_xml_declaration_encoding() -> None:
    page = '<?xml version="1.0" encoding="ISO-8859-5"?><html><body><p>Склад открыт</p></body></html>'
    assert "Склад открыт" in decode_web_text(page.encode("iso-8859-5"), "application/xhtml+xml")


def test_bom_beats_a_wrong_declaration() -> None:
    page = '<meta charset="iso-8859-1"><p>दिल्ली गोदाम</p>'
    assert "दिल्ली गोदाम" in decode_web_text(b"\xef\xbb\xbf" + page.encode("utf-8"), "text/html")
    assert "दिल्ली गोदाम" in decode_web_text(page.encode("utf-16"), "text/html")


def test_undeclared_utf8_and_undeclared_legacy() -> None:
    assert decode_web_text("Ünïcödé ✓".encode(), "text/plain") == "Ünïcödé ✓"
    # No declaration anywhere and not UTF-8: never U+FFFD soup for Western text.
    assert decode_web_text("Café crème brûlée".encode("cp1252"), "text/plain") \
        == "Café crème brûlée"


def test_undeclared_cyrillic_and_japanese_are_detected() -> None:
    ru = "Склад в Новосибирске открыт с девяти утра до шести вечера по будним дням. " * 3
    ja = "東京都江東区の倉庫は平日の午前九時から午後六時まで営業しています。" * 3
    assert decode_web_text(ru.encode("cp1251"), "text/plain") == ru
    assert decode_web_text(ja.encode("shift_jis"), "text/plain") == ja


def test_unknown_charset_label_falls_back() -> None:
    assert decode_web_text(b"plain ascii", "text/plain; charset=x-bogus-9") == "plain ascii"


# ── kind ────────────────────────────────────────────────────────────────────


def _zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("url", "ctype", "data", "ext"),
    [
        ("https://h/files/report", "application/octet-stream", b"%PDF-1.7 ...", "pdf"),
        ("https://h/r.pdf", "application/pdf", b"%PDF-1.4", "pdf"),
        ("https://h/download?id=7", "application/octet-stream",
         _zip(["[Content_Types].xml", "word/document.xml"]), "docx"),
        ("https://h/deck", "", _zip(["[Content_Types].xml", "ppt/presentation.xml"]), "pptx"),
        ("https://h/book", "", _zip(["[Content_Types].xml", "xl/workbook.xml"]), "xlsx"),
        ("https://h/a.zip", "application/zip", _zip(["a.txt"]), "zip"),
        ("https://h/page", "text/html; charset=utf-8", b"<html><body>x</body></html>", "html"),
        ("https://h/page.txt", "text/plain", b"<!DOCTYPE html><html>mislabelled</html>", "html"),
        ("https://h/notes.txt", "text/plain", b"plain notes", "txt"),
        ("https://h/readme.md", "text/plain", b"# Title\n\ntext", "md"),
        ("https://h/data", "text/csv", b"a,b\n1,2\n", "csv"),
        ("https://h/api", "application/json", b'{"a": 1}', "json"),
        ("https://h/img", "image/png", b"\x89PNG\r\n\x1a\n....", "png"),
        ("https://h/x.xhtml", "application/xhtml+xml", b"<?xml version='1.0'?><html/>", "html"),
    ],
)
def test_resource_kind(url: str, ctype: str, data: bytes, ext: str) -> None:
    assert web_resource_ext(url, ctype, data) == ext


# ── streaming fetch with a size cap ─────────────────────────────────────────


def _site(routes: dict[str, tuple[int, dict[str, str], bytes]],
          seen: list[str]) -> Callable[..., Any]:
    async def _send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        status, headers, body = routes.get(url, (404, {}, b"missing"))
        return httpx.Response(status, headers=headers, content=body, request=request)

    return _send


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """example.org resolves to a public address (no real DNS in unit tests)."""
    import socket

    real = socket.getaddrinfo

    def fake(host: Any, *args: Any, **kwargs: Any) -> Any:
        if str(host).endswith("example.org"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


async def _fetch(routes: dict[str, tuple[int, dict[str, str], bytes]], url: str,
                 **kw: Any) -> tuple[Any, list[str]]:
    from app.ingestion.connector_egress import source_client

    seen: list[str] = []
    with patch.object(httpx.AsyncClient, "send", _site(routes, seen)):
        async with source_client(timeout=5) as client:
            return await fetch_web_resource(client, url, context="test", **kw), seen


@pytest.mark.asyncio
async def test_fetch_follows_a_permanent_redirect_and_records_the_move(public_dns: None) -> None:
    old, new = "https://example.org/old", "https://example.org/new"
    res, seen = await _fetch({old: (301, {"location": "/new"}, b""),
                              new: (200, {"content-type": "text/plain"}, b"moved here")}, old,
                             max_bytes=1000)
    assert res.data == b"moved here"
    assert res.final_url == new
    assert res.moved == {"from": old, "to": new, "status": 301}
    assert seen == [old, new]


@pytest.mark.asyncio
async def test_fetch_refuses_a_body_over_the_cap(public_dns: None) -> None:
    url = "https://example.org/huge"
    with pytest.raises(WebFetchError) as err:
        await _fetch({url: (200, {"content-type": "text/html"}, b"x" * 5000)}, url, max_bytes=1000)
    assert err.value.kind == "too_large"
    assert not err.value.retryable
    assert err.value.http_status == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "retryable", "http"), [(404, False, 502), (410, False, 502),
                                                          (500, True, 502), (503, True, 502),
                                                          (429, True, 502)])
async def test_fetch_http_errors_are_typed(public_dns: None, status: int, retryable: bool,
                                           http: int) -> None:
    url = "https://example.org/err"
    with pytest.raises(WebFetchError) as err:
        await _fetch({url: (status, {}, b"oops")}, url, max_bytes=1000)
    assert err.value.kind == "http"
    assert err.value.upstream_status == status
    assert err.value.retryable is retryable
    assert err.value.http_status == http
    assert f"HTTP {status}" in str(err.value)


@pytest.mark.asyncio
async def test_fetch_redirect_to_an_internal_address_is_refused(public_dns: None) -> None:
    url = "https://example.org/r"
    with pytest.raises(WebFetchError) as err:
        await _fetch({url: (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b"")},
                     url, max_bytes=1000)
    assert err.value.kind == "blocked"
    assert err.value.http_status == 400
    assert not err.value.retryable


@pytest.mark.asyncio
async def test_fetch_redirect_loop_is_an_honest_error(public_dns: None) -> None:
    a, b = "https://example.org/a", "https://example.org/b"
    with pytest.raises(WebFetchError) as err:
        await _fetch({a: (302, {"location": b}, b""), b: (302, {"location": a}, b"")}, a,
                     max_bytes=1000)
    assert err.value.kind == "redirect_loop"
    assert err.value.http_status == 422


@pytest.mark.asyncio
async def test_fetch_timeout_is_retryable_504(public_dns: None) -> None:
    from app.ingestion.connector_egress import source_client

    async def _slow(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with patch.object(httpx.AsyncClient, "send", _slow):
        async with source_client(timeout=5) as client:
            with pytest.raises(WebFetchError) as err:
                await fetch_web_resource(client, "https://example.org/slow", context="t",
                                         max_bytes=10)
    assert err.value.kind == "timeout"
    assert err.value.retryable
    assert err.value.http_status == 504
