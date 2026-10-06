"""Scanned pages of an upload are OCR'd concurrently, bounded (OCR-PAR-3).

``ocr_pdf_pages`` rendered and OCR'd the textless pages of an upload one after
another (render in the default executor, PNG encode on the event loop, then the
engine). Now each page renders and OCRs on the process-wide OCR pool, at most
OCR_PAGE_CONCURRENCY pages of the upload at once, and every page's text stays
on its own page number (citations never shift). Failures stay fail-closed: a
page that cannot be rendered or a budget refusal fails the call, and the other
pages stop.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from app.ingestion import document_text as dt
from app.ocr import concurrency as oc
from app.ocr.models import DocumentType, OcrResult


@pytest.fixture(autouse=True)
def _pool() -> Any:
    oc.reset_ocr_concurrency(oc.OcrLimits(4, 3, 2, 300))
    with patch("app.ingestion.document_text.tesseract_available", return_value=True):
        yield
    oc.reset_ocr_concurrency()


class _Fake:
    def __init__(self, *, delay: float = 0.03, fail_render: int | None = None) -> None:
        self.delay = delay
        self.fail_render = fail_render
        self.lock = threading.Lock()
        self.sources: list[Any] = []
        self.render_threads: set[str] = set()
        self.dpis: set[int] = set()
        self.in_flight = 0
        self.peak = 0
        self.extracted: list[int] = []

    def render(self, data: Any, page_number: int, *, dpi: int = 300) -> Any:
        with self.lock:
            self.sources.append(data)
            self.render_threads.add(threading.current_thread().name)
            self.dpis.add(dpi)
        time.sleep(0.01)
        if page_number == self.fail_render:
            raise dt.DocumentParseError(f"page {page_number} could not be rendered for OCR")
        return Image.new("L", (100 + page_number, 30), 255)

    async def extract(self, *, image_bytes: bytes, **_: Any) -> OcrResult:
        page = Image.open(io.BytesIO(image_bytes)).size[0] - 100
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.extracted.append(page)
        try:
            await asyncio.sleep(self.delay * (1 + (7 - page) % 3))
        finally:
            with self.lock:
                self.in_flight -= 1
        return OcrResult(raw_text=f"  scanned text of page {page}  ",
                         document_type=DocumentType.GENERAL, engine_used="tesseract",
                         overall_confidence=0.9, page_count=1)


async def _run(fake: _Fake, pages: list[int]) -> dict[int, tuple[str, str]]:
    with patch("app.ingestion.document_text.render_pdf_page", fake.render):
        return await dt.ocr_pdf_pages(b"%PDF-1.4 scan", filename="scan.pdf",
                                      page_numbers=pages, ocr_engine=fake)


async def test_scanned_pages_are_ocrd_concurrently_each_on_its_own_page() -> None:
    fake = _Fake()
    pages = [2, 3, 5, 6, 7, 9, 11]
    out = await _run(fake, pages)
    assert list(out) == pages
    assert out == {p: (f"scanned text of page {p}", "tesseract") for p in pages}
    assert fake.peak == 3  # OCR_PAGE_CONCURRENCY, never more
    assert sorted(fake.extracted) == pages


async def test_pages_render_on_the_ocr_pool_from_one_file_at_the_configured_dpi() -> None:
    fake = _Fake()
    ticks = 0
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    beat = asyncio.create_task(_heartbeat())
    await _run(fake, [1, 2, 3, 4])
    stop.set()
    await beat
    assert fake.render_threads and all(n.startswith("ocr") for n in fake.render_threads)
    assert ticks >= 5
    assert fake.dpis == {300}
    # The PDF is written once and every page renders from that file.
    assert len({str(s) for s in fake.sources}) == 1
    assert all(isinstance(s, Path) for s in fake.sources)
    assert not Path(fake.sources[0]).exists()  # cleaned up afterwards


async def test_a_page_that_cannot_be_rendered_fails_the_call_and_stops_the_rest() -> None:
    fake = _Fake(fail_render=3, delay=0.2)
    with pytest.raises(dt.DocumentParseError, match="page 3"):
        await _run(fake, list(range(1, 13)))
    assert len(fake.extracted) < 12
    await asyncio.sleep(0.05)
    assert oc.page_slots_in_use() == 0


async def test_budget_refusal_on_one_page_fails_closed() -> None:
    from app.providers.guarded_completion import DecisionBudgetExceededError

    fake = _Fake()

    async def _extract(*, image_bytes: bytes, **kw: Any) -> OcrResult:
        page = Image.open(io.BytesIO(image_bytes)).size[0] - 100
        if page == 2:
            raise DecisionBudgetExceededError("budget exhausted")
        return await _Fake.extract(fake, image_bytes=image_bytes, **kw)

    fake.extract = _extract  # type: ignore[method-assign]
    with pytest.raises(DecisionBudgetExceededError):
        await _run(fake, [1, 2, 3, 4, 5, 6])


async def test_too_many_scanned_pages_is_still_refused_before_any_work() -> None:
    fake = _Fake()
    with pytest.raises(dt.DocumentParseError, match="split the PDF"):
        await _run(fake, list(range(1, dt.OCR_MAX_PDF_PAGES + 2)))
    assert fake.sources == []


def test_render_pdf_page_accepts_a_path_and_maps_poppler_errors(tmp_path: Path) -> None:
    pytest.importorskip("pdf2image")  # the optional 'ocr' extra
    from pdf2image.exceptions import PDFInfoNotInstalledError

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    calls: list[tuple[str, dict[str, Any]]] = []

    def _from_path(path: str, **kw: Any) -> list[Any]:
        calls.append((path, kw))
        return [Image.new("L", (5, 5), 255)]

    with patch("pdf2image.convert_from_path", _from_path):
        assert dt.render_pdf_page(pdf, 4, dpi=200).size == (5, 5)
    assert calls == [(str(pdf), {"dpi": 200, "first_page": 4, "last_page": 4,
                                 "grayscale": True})]

    def _missing(*_a: Any, **_k: Any) -> list[Any]:
        raise PDFInfoNotInstalledError("no pdfinfo")

    with (
        patch("pdf2image.convert_from_path", _missing),
        pytest.raises(dt.ParserUnavailableError, match="poppler"),
    ):
        dt.render_pdf_page(pdf, 1)
