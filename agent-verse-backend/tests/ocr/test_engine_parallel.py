"""OcrEngine OCRs the pages of a document concurrently, bounded (OCR-PAR-2).

Before: ``extract`` OCR'd page after page, and a PDF was rasterised whole
(``convert_from_bytes`` of every page) synchronously ON the event loop, holding
every page bitmap at once. Now pages run concurrently under the per-document cap
and the process-wide page slots, each page is rendered on its own in the OCR pool,
and every page's text stays attached to its own page.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from app.ocr import concurrency as oc
from app.ocr.engine import OcrEngine


@pytest.fixture(autouse=True)
def _pool() -> Any:
    oc.reset_ocr_concurrency(oc.OcrLimits(4, 3, 2, 300))
    yield
    oc.reset_ocr_concurrency()


def _page_image(n: int) -> Image.Image:
    """Page n's bitmap: its width encodes the page number."""
    return Image.new("L", (100 + n, 40), 255)


def _page_no(img: Any) -> int:
    return int(img.size[0]) - 100


class _Pages:
    """Fake rasteriser + page OCR that records overlap and thread use."""

    def __init__(self, count: int, *, delay: float = 0.03) -> None:
        self.count = count
        self.delay = delay
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.rendered: list[int] = []
        self.render_threads: set[str] = set()
        self.alive_bitmaps = 0
        self.peak_bitmaps = 0

    def page_count(self, path: Any) -> int:
        return self.count

    def render(self, path: Any, page_number: int, *, dpi: int) -> Any:
        with self.lock:
            self.rendered.append(page_number)
            self.render_threads.add(threading.current_thread().name)
            self.alive_bitmaps += 1
            self.peak_bitmaps = max(self.peak_bitmaps, self.alive_bitmaps)
        time.sleep(0.01)  # blocking work: must not run on the event loop
        return _page_image(page_number)

    async def ocr_page(self, engine: Any, img: Any, *, provider: Any = None,
                       vision_fallback: bool = True) -> tuple[str, float, str]:
        n = _page_no(img)
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        # Later pages finish first, so completion order differs from page order.
        await asyncio.sleep(self.delay * (1 + (self.count - n) % 4))
        with self.lock:
            self.in_flight -= 1
            self.alive_bitmaps -= 1
        return f"text of page {n}", 0.5 + n / 100, "tesseract"


def _patched(pages: _Pages) -> Any:
    from contextlib import ExitStack

    async def _ocr_page(engine: Any, img: Any, **kw: Any) -> tuple[str, float, str]:
        return await pages.ocr_page(engine, img, **kw)

    stack = ExitStack()
    stack.enter_context(patch("app.ocr.engine.pdf_page_count", pages.page_count))
    stack.enter_context(patch("app.ocr.engine.render_pdf_page_image", pages.render))
    stack.enter_context(patch.object(OcrEngine, "_ocr_page", _ocr_page))
    return stack


async def test_pages_of_one_pdf_are_ocrd_concurrently_in_page_order() -> None:
    pages = _Pages(10)
    with _patched(pages):
        result = await OcrEngine().extract(pdf_bytes=b"%PDF-fake", extract_fields=False)
    assert result.page_count == 10
    # Each page's text stays on its own page, in page order.
    assert result.raw_text.split("\n\n") == [f"text of page {n}" for n in range(1, 11)]
    assert pages.peak == 3  # OCR_PAGE_CONCURRENCY, never more
    assert sorted(pages.rendered) == list(range(1, 11))  # one render per page
    # Overall confidence / engine semantics unchanged: mean over pages, page 1's engine.
    assert result.overall_confidence == pytest.approx(sum(0.5 + n / 100 for n in range(1, 11)) / 10)
    assert result.engine_used == "tesseract"


async def test_a_large_scan_never_holds_every_page_bitmap() -> None:
    pages = _Pages(40, delay=0.005)
    with _patched(pages):
        result = await OcrEngine().extract(pdf_bytes=b"%PDF-fake", extract_fields=False)
    assert result.page_count == 40
    assert pages.peak_bitmaps <= 3  # at most the pages in flight


async def test_rasterisation_runs_in_the_ocr_pool_not_on_the_event_loop() -> None:
    pages = _Pages(6)
    ticks = 0
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    beat = asyncio.create_task(_heartbeat())
    with _patched(pages):
        await OcrEngine().extract(pdf_bytes=b"%PDF-fake", extract_fields=False)
    stop.set()
    await beat
    assert pages.render_threads and all(n.startswith("ocr") for n in pages.render_threads)
    assert ticks >= 5


async def test_real_render_is_page_by_page_at_the_configured_dpi() -> None:
    """The real rasteriser is asked for ONE page at a time (first_page ==
    last_page), grayscale, at OCR_RENDER_DPI — never the whole document."""
    pytest.importorskip("pdf2image")  # the optional 'ocr' extra
    calls: list[dict[str, Any]] = []

    def _convert(path: Any, **kw: Any) -> list[Any]:
        calls.append(kw)
        return [_page_image(int(kw["first_page"]))]

    async def _ocr_page(engine: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        return f"p{_page_no(img)}", 0.9, "tesseract"

    with (
        patch("pdf2image.convert_from_path", _convert),
        patch("app.ocr.engine.pdf_page_count", lambda _p: 3),
        patch.object(OcrEngine, "_ocr_page", _ocr_page),
    ):
        result = await OcrEngine().extract(pdf_bytes=b"%PDF-fake", extract_fields=False)
    assert result.raw_text == "p1\n\np2\n\np3"
    assert sorted((c["first_page"], c["last_page"]) for c in calls) == [(1, 1), (2, 2), (3, 3)]
    assert {c["dpi"] for c in calls} == {300}
    assert all(c.get("grayscale") for c in calls)


async def test_two_documents_interleave_a_huge_one_does_not_starve_a_small_one() -> None:
    """Global slots 4, per-document 3: while a 40-page scan is being OCR'd, a
    2-page document finishes long before the scan does."""
    big = _Pages(40, delay=0.02)
    finished: list[str] = []

    async def _ocr_page(engine: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        n = _page_no(img)
        await asyncio.sleep(0.02)
        return f"page {n}", 0.9, "tesseract"

    async def _doc(name: str, count: int) -> None:
        with patch("app.ocr.engine.pdf_page_count", lambda _p: count):
            res = await OcrEngine().extract(pdf_bytes=name.encode(), extract_fields=False)
        assert res.page_count == count
        finished.append(name)

    with (
        patch("app.ocr.engine.render_pdf_page_image", big.render),
        patch.object(OcrEngine, "_ocr_page", _ocr_page),
    ):
        big_task = asyncio.create_task(_doc("big", 40))
        await asyncio.sleep(0.05)  # the big scan holds its page slots
        await _doc("small", 2)
        assert not big_task.done()  # the small one did not wait for the scan
        await big_task
    assert finished == ["small", "big"]


async def test_page_that_fails_to_render_is_an_empty_page_not_a_lost_document() -> None:
    def _render(path: Any, page_number: int, *, dpi: int) -> Any:
        if page_number == 2:
            raise RuntimeError("pdftoppm: page 2 is damaged")
        return _page_image(page_number)

    async def _ocr_page(engine: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        return f"page {_page_no(img)}", 0.9, "tesseract"

    with (
        patch("app.ocr.engine.pdf_page_count", lambda _p: 3),
        patch("app.ocr.engine.render_pdf_page_image", _render),
        patch.object(OcrEngine, "_ocr_page", _ocr_page),
    ):
        result = await OcrEngine().extract(pdf_bytes=b"%PDF-fake", extract_fields=False)
    assert result.page_count == 3
    assert result.raw_text == "page 1\n\n\n\npage 3"


async def test_unreadable_pdf_still_degrades_to_no_pages() -> None:
    def _count(_p: Any) -> int:
        raise RuntimeError("pdfinfo: not a PDF")

    with patch("app.ocr.engine.pdf_page_count", _count):
        result = await OcrEngine().extract(pdf_bytes=b"garbage", extract_fields=False)
    assert result.page_count == 0 and result.raw_text == ""


# ── Tesseract work and the LLM-vision fallback ───────────────────────────────


class _SlowTess:
    """pytesseract stand-in: blocking calls, records threads and overlap."""

    TesseractError = RuntimeError

    def __init__(self, conf: int = 95) -> None:
        self.conf = conf
        self.lock = threading.Lock()
        self.threads: set[str] = set()
        self.running = 0
        self.peak = 0

    class Output:
        DICT = "dict"

    def image_to_data(self, img: Any, lang: str = "eng", output_type: Any = None) -> Any:
        with self.lock:
            self.threads.add(threading.current_thread().name)
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(0.03)
        with self.lock:
            self.running -= 1
        return {"text": [f"w{img.size[0]}"], "conf": [self.conf]}

    def image_to_osd(self, img: Any, output_type: Any = None) -> Any:
        raise RuntimeError("Too few characters")

    def get_languages(self, config: str = "") -> list[str]:
        return ["eng"]


async def test_tesseract_runs_on_the_ocr_pool_bounded_by_the_global_cap() -> None:
    tess = _SlowTess()
    with (
        patch.dict("sys.modules", {"pytesseract": tess}),
        patch("app.ocr.engine.pdf_page_count", lambda _p: 8),
        patch("app.ocr.engine.render_pdf_page_image",
              lambda _p, n, *, dpi: _page_image(n).convert("RGB")),
    ):
        docs = await asyncio.gather(*(
            OcrEngine().extract(pdf_bytes=b"%PDF-" + bytes([i]), extract_fields=False)
            for i in range(3)
        ))
    assert all(d.page_count == 8 for d in docs)
    assert docs[0].raw_text.split("\n\n") == [f"w{100 + n}" for n in range(1, 9)]
    assert tess.threads and all(n.startswith("ocr") for n in tess.threads)
    assert tess.peak <= 4  # OCR_MAX_CONCURRENCY across all three documents


class _VisionProvider:
    def __init__(self, fail_page: int | None = None) -> None:
        self.fail_page = fail_page
        self.in_flight = 0
        self.peak = 0
        self.calls = 0


def _vision_patch(vp: _VisionProvider, *, budget_page: int | None = None) -> Any:
    async def _vision(engine: Any, img: Any, *, provider: Any = None,
                      tenant_id: str | None = None) -> tuple[str, float, str]:
        n = _page_no(img)
        vp.calls += 1
        vp.in_flight += 1
        vp.peak = max(vp.peak, vp.in_flight)
        try:
            await asyncio.sleep(0.03)
            if budget_page == n:
                from app.providers.guarded_completion import DecisionBudgetExceededError

                raise DecisionBudgetExceededError("budget exhausted")
            if vp.fail_page == n:
                return "", 0.0, "llm_vision"  # the engine's own per-page failure result
            return f"vision page {n}", 0.85, "llm_vision"
        finally:
            vp.in_flight -= 1

    return patch.object(OcrEngine, "_llm_vision_ocr_unbounded", _vision)


async def test_vision_fallback_pages_run_concurrently_but_bounded() -> None:
    provider = _VisionProvider(fail_page=3)
    tess = _SlowTess(conf=10)  # every page is low confidence → vision fallback
    with (
        patch.dict("sys.modules", {"pytesseract": tess}),
        patch("app.ocr.engine.pdf_page_count", lambda _p: 6),
        patch("app.ocr.engine.render_pdf_page_image", lambda _p, n, *, dpi: _page_image(n)),
        _vision_patch(provider),
    ):
        result = await OcrEngine().extract(pdf_bytes=b"%PDF-v", provider=object(),
                                           extract_fields=False)
    assert provider.calls == 6
    assert provider.peak == 2  # OCR_VISION_CONCURRENCY
    # Page 3's vision failure falls back to that page's own (weak) Tesseract
    # reading instead of an empty page; every other page keeps its vision text.
    pages = result.raw_text.split("\n\n")
    assert pages[:2] == ["vision page 1", "vision page 2"]
    assert pages[2] == "w103"
    assert pages[3:] == ["vision page 4", "vision page 5", "vision page 6"]
    assert result.engine_used == "mixed"
    assert result.page_engines[2] == "tesseract"
    assert result.vision_pages == 5


async def test_vision_budget_refusal_fails_closed_and_stops_the_other_pages() -> None:
    from app.providers.guarded_completion import DecisionBudgetExceededError

    provider = _VisionProvider()
    tess = _SlowTess(conf=10)
    with (
        patch.dict("sys.modules", {"pytesseract": tess}),
        patch("app.ocr.engine.pdf_page_count", lambda _p: 12),
        patch("app.ocr.engine.render_pdf_page_image", lambda _p, n, *, dpi: _page_image(n)),
        _vision_patch(provider, budget_page=1),
        pytest.raises(DecisionBudgetExceededError),
    ):
        await OcrEngine().extract(pdf_bytes=b"%PDF-b", provider=object(), extract_fields=False)
    assert provider.calls < 12  # no further vision spend after the refusal
    await asyncio.sleep(0.1)
    assert oc.page_slots_in_use() == 0


async def test_office_conversion_runs_off_the_event_loop_with_its_own_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LibreOffice conversion (up to 120 s) ran subprocess.run ON the event loop,
    and concurrent conversions shared one LibreOffice profile (which fails)."""
    import subprocess

    seen: list[tuple[str, list[str]]] = []

    def _run(cmd: list[str], **_: Any) -> Any:
        seen.append((threading.current_thread().name, list(cmd)))
        out = cmd[cmd.index("--outdir") + 1]
        src = cmd[-1]
        from pathlib import Path

        Path(out, Path(src).with_suffix(".pdf").name).write_bytes(b"%PDF-converted")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("app.ocr.engine.shutil.which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr("app.ocr.engine.subprocess.run", _run)

    async def _extract(self: Any, **kw: Any) -> Any:
        from app.ocr.models import DocumentType, OcrResult

        assert kw["pdf_bytes"] == b"%PDF-converted"
        return OcrResult(raw_text="ok", document_type=DocumentType.GENERAL,
                         overall_confidence=0.9, page_count=1)

    monkeypatch.setattr(OcrEngine, "extract", _extract)
    results = await asyncio.gather(*(
        OcrEngine().extract_any(b"PK\x03\x04", filename=f"d{i}.docx") for i in range(2)
    ))
    assert [r.raw_text for r in results] == ["ok", "ok"]
    assert len(seen) == 2
    assert all(name.startswith("ocr") for name, _ in seen)
    profiles = [next(a for a in cmd if a.startswith("-env:UserInstallation=")) for _, cmd in seen]
    assert len(set(profiles)) == 2  # each conversion has its own LibreOffice profile
