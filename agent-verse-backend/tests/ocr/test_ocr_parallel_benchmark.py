"""Real Tesseract + poppler: parallel OCR is correct per page, and faster (OCR-PAR).

Opt-in (``-m slow``): needs the tesseract and pdftoppm binaries (both are in the
backend image). Builds real scanned documents (text rendered to bitmaps, PDF
pages with no text layer), then OCRs them sequentially (a one-thread pool) and
with the default pool, and checks that every page's text is read from THAT page,
and that the event loop keeps serving while documents are OCR'd.

Run in the image:  docker run --rm <image> python -m pytest -m slow -s --no-cov \
    -p no:cacheprovider tests/ocr/test_ocr_parallel_benchmark.py
"""

from __future__ import annotations

import asyncio
import io
import shutil
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.ocr import concurrency as oc

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (shutil.which("tesseract") and shutil.which("pdftoppm")),
        reason="needs the tesseract and poppler binaries",
    ),
]

_WORDS = [
    "ANCHOR", "BALLAST", "CAPSTAN", "DAVIT", "ENSIGN", "FATHOM", "GALLEY", "HAWSER",
    "JETTY", "KEDGE", "LANYARD", "MOORING", "NAVIGATOR", "OFFSHORE", "PILOT", "QUARTER",
    "RUDDER", "SEXTANT", "TILLER", "UPWIND", "VOYAGE", "WINDLASS", "YARDARM", "ZENITH",
]


def _page_bitmap(doc: int, page: int) -> Any:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (1700, 2200), 255)  # US Letter at 200 dpi
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=44)
    word = _WORDS[(doc * 7 + page) % len(_WORDS)]
    lines = [
        f"HARBOUR PERMIT {doc:02d}{page:02d}",
        f"PAGE {page} KEYWORD {word}",
        "The vessel may berth at quay four after the pilot boards.",
        "Cargo handling is permitted between six and eighteen hours.",
        "Ballast water must not be discharged inside the harbour.",
    ] * 3
    y = 150
    for line in lines:
        draw.text((140, y), line, fill=0, font=font)
        y += 110
    return img


def _scan_pdf(doc: int, pages: int) -> bytes:
    bitmaps = [_page_bitmap(doc, p) for p in range(1, pages + 1)]
    buf = io.BytesIO()
    bitmaps[0].save(buf, format="PDF", save_all=True, append_images=bitmaps[1:],
                    resolution=200.0)
    return buf.getvalue()


def _expected(doc: int, page: int) -> tuple[str, str]:
    return f"{doc:02d}{page:02d}", _WORDS[(doc * 7 + page) % len(_WORDS)]


def _assert_page(text: str, doc: int, page: int) -> None:
    permit, word = _expected(doc, page)
    assert permit in text and word in text, (doc, page, text[:200])


async def _timed(limits: oc.OcrLimits, run: Callable[[], Awaitable[Any]]) -> tuple[float, float, Any]:
    """(wall seconds, worst event-loop stall seconds, result) under ``limits``."""
    oc.reset_ocr_concurrency(limits)
    stall = 0.0
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        nonlocal stall
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            stall = max(stall, now - last - 0.01)
            last = now

    beat = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0)  # the heartbeat runs before the work starts
    start = time.perf_counter()
    try:
        result = await run()
    finally:
        elapsed = time.perf_counter() - start
        stop.set()
        await beat
        oc.reset_ocr_concurrency()
    return elapsed, stall, result


def _limits(parallel: bool) -> oc.OcrLimits:
    if not parallel:
        return oc.OcrLimits(1, 1, 1, 200)
    g = oc.available_cpus()
    return oc.OcrLimits(g, oc.default_page_concurrency(g), 4, 200)


def _report(name: str, seq: tuple[float, float, Any], par: tuple[float, float, Any]) -> None:
    print(  # benchmark output (run with -s)
        f"\n[OCR-PAR bench] {name}: sequential {seq[0]:.2f}s (max loop stall {seq[1]:.3f}s)"
        f" | parallel {par[0]:.2f}s (max loop stall {par[1]:.3f}s)"
        f" | speedup x{seq[0] / par[0]:.2f} | cpus {oc.available_cpus()}"
    )


async def test_one_twenty_page_scan_engine() -> None:
    from app.ocr.engine import OcrEngine

    pdf = _scan_pdf(1, 20)

    async def _run() -> Any:
        return await OcrEngine().extract(pdf_bytes=pdf, extract_fields=False,
                                         vision_fallback=False)

    seq = await _timed(_limits(False), _run)
    par = await _timed(_limits(True), _run)
    _report("1 x 20-page scan (OcrEngine.extract)", seq, par)
    for _elapsed, _stall, result in (seq, par):
        pages = result.raw_text.split("\n\n")
        assert result.page_count == 20 and len(pages) == 20
        for n, text in enumerate(pages, start=1):
            _assert_page(text, 1, n)
    assert seq[2].raw_text == par[2].raw_text  # identical text, identical order
    assert par[1] < 0.5  # the loop keeps serving while pages render / OCR
    if oc.available_cpus() >= 2:
        assert par[0] < seq[0]


async def test_five_documents_of_four_pages_at_once() -> None:
    from app.ocr.engine import OcrEngine

    pdfs = {d: _scan_pdf(d, 4) for d in range(1, 6)}

    async def _run() -> Any:
        return await asyncio.gather(*(
            OcrEngine().extract(pdf_bytes=pdfs[d], extract_fields=False, vision_fallback=False)
            for d in pdfs
        ))

    seq = await _timed(_limits(False), _run)
    par = await _timed(_limits(True), _run)
    _report("5 docs x 4 pages concurrently", seq, par)
    for _elapsed, _stall, results in (seq, par):
        for d, result in zip(pdfs, results, strict=True):
            pages = result.raw_text.split("\n\n")
            assert len(pages) == 4
            for n, text in enumerate(pages, start=1):
                _assert_page(text, d, n)
    if oc.available_cpus() >= 2:
        assert par[0] < seq[0]


async def test_upload_scanned_pages_keep_their_page_numbers() -> None:
    from app.ingestion.document_text import ocr_pdf_pages

    pdf = _scan_pdf(7, 12)
    pages = list(range(1, 13))

    async def _run() -> Any:  # rendered at the pool's render_dpi (200 here)
        return await ocr_pdf_pages(pdf, filename="scan.pdf", page_numbers=pages)

    seq = await _timed(_limits(False), _run)
    par = await _timed(_limits(True), _run)
    _report("upload ocr_pdf_pages, 12 scanned pages", seq, par)
    for _elapsed, _stall, out in (seq, par):
        assert list(out) == pages
        for n in pages:
            _assert_page(out[n][0], 7, n)
            assert out[n][1] == "tesseract"


async def test_several_images_at_once() -> None:
    from app.ingestion.document_text import extract_image_text

    images = {}
    for d in range(1, 7):
        buf = io.BytesIO()
        _page_bitmap(d, 1).save(buf, format="PNG")
        images[d] = buf.getvalue()

    async def _run() -> Any:
        return await asyncio.gather(*(
            extract_image_text(images[d], filename=f"img{d}.png") for d in images
        ))

    seq = await _timed(_limits(False), _run)
    par = await _timed(_limits(True), _run)
    _report("6 images at once (extract_image_text)", seq, par)
    for _elapsed, _stall, results in (seq, par):
        for d, (text, engine) in zip(images, results, strict=True):
            _assert_page(text, d, 1)
            assert engine == "tesseract"
