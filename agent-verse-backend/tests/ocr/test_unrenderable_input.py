"""An input nothing can be read from is never a silent zero-page result.

A PDF that could not be rendered (corrupt, truncated, encrypted, zero pages)
used to come back as ``page_count=0``, empty text and ``degraded=False`` — the
API then answered a plain 200. The engine now records WHY (``failure_kind`` +
``degradation_reason``): ``invalid_input`` when the document itself cannot be
opened (pypdf confirms it, independently of poppler), ``engine_failed`` when the
renderer failed on — or is missing for — a document pypdf can read.

Inputs are real generated documents (tests/ocr/_pdf_inputs.py). Poppler is not
installed on dev machines, so its answer is simulated with the exception
pdf2image raises (probed against poppler 22.12 in the backend image); the last
test runs the real renderer where it is installed.
"""

from __future__ import annotations

import shutil
from typing import Any
from unittest.mock import patch

import pytest

from app.ocr.engine import OcrEngine, _note_page_failure
from app.ocr.rasterize import describe_render_failure, pdf_structure_problem
from tests.ocr._pdf_inputs import (
    PDFPopplerTimeoutError,
    corrupt_pdf,
    encrypted_pdf,
    page_image,
    poppler_refuses,
    truncated_pdf,
    valid_pdf,
    zero_page_pdf,
)


async def _ocr_text(_engine: Any, img: Any, **_: Any) -> tuple[str, float, str]:
    return f"page {img.info.get('page', 1)} text", 0.9, "tesseract"


async def _extract_pdf(data: bytes, count: Any = poppler_refuses, render: Any = None) -> Any:
    with (
        patch("app.ocr.engine.pdf_page_count", count),
        patch(
            "app.ocr.engine.render_pdf_page_image",
            render or (lambda _p, n, *, dpi: page_image(n)),
        ),
        patch.object(OcrEngine, "_ocr_page", _ocr_text),
    ):
        return await OcrEngine().extract(pdf_bytes=data, extract_fields=False)


# ── pypdf structure check (independent of poppler) ───────────────────────────


def test_structure_check_accepts_a_valid_pdf() -> None:
    assert pdf_structure_problem(valid_pdf()) is None


@pytest.mark.parametrize(
    ("data", "kind", "words"),
    [
        (corrupt_pdf(), "damaged", "corrupt or truncated"),
        (truncated_pdf(), "damaged", "corrupt or truncated"),
        (b"hello, not a pdf", "damaged", "not a PDF"),
        (b"", "damaged", "empty"),
        (encrypted_pdf(), "encrypted", "password"),
        (zero_page_pdf(), "no_pages", "no pages"),
    ],
)
def test_structure_check_names_the_problem(data: bytes, kind: str, words: str) -> None:
    problem = pdf_structure_problem(data)
    assert problem is not None
    assert problem[0] == kind
    assert words in problem[1]


def test_owner_password_only_pdf_opens() -> None:
    """Encrypted with a blank user password (print/copy restrictions only): every
    viewer opens it without a password, so it is not refused."""
    assert pdf_structure_problem(encrypted_pdf(user_password="")) is None


def test_render_failure_description_carries_poppler_detail() -> None:
    from tests.ocr._pdf_inputs import PDFPageCountError

    msg = describe_render_failure(
        PDFPageCountError("Unable to get page count.\nCommand Line Error: Incorrect password\n")
    )
    assert "Incorrect password" in msg
    assert "timed out" in describe_render_failure(PDFPopplerTimeoutError("x"))


# ── unopenable PDFs: invalid_input, never a silent zero-page result ──────────


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (corrupt_pdf(), "corrupt or truncated"),
        (truncated_pdf(), "corrupt or truncated"),
        (encrypted_pdf(), "encrypted"),
        (zero_page_pdf(), "no pages"),
    ],
    ids=["corrupt", "truncated", "encrypted", "zero-pages"],
)
async def test_unopenable_pdf_is_invalid_input_with_the_reason(data: bytes, words: str) -> None:
    result = await _extract_pdf(data)
    assert result.page_count == 0
    assert result.raw_text == ""
    assert result.degraded is True
    assert result.failure_kind == "invalid_input"
    assert words in (result.degradation_reason or "")


async def test_corrupt_pdf_without_any_renderer_is_still_invalid_input() -> None:
    """pypdf alone already proves the input cannot be opened."""
    with patch.dict("sys.modules", {"pdf2image": None}):
        result = await OcrEngine().extract(pdf_bytes=truncated_pdf(), extract_fields=False)
    assert result.failure_kind == "invalid_input"
    assert "corrupt or truncated" in (result.degradation_reason or "")


# ── a readable PDF the renderer failed on: engine_failed ─────────────────────


async def test_valid_pdf_with_no_renderer_installed_is_an_engine_failure() -> None:
    with patch.dict("sys.modules", {"pdf2image": None}):
        result = await OcrEngine().extract(pdf_bytes=valid_pdf(), extract_fields=False)
    assert result.page_count == 0
    assert result.degraded is True
    assert result.failure_kind == "engine_failed"
    assert "not installed" in (result.degradation_reason or "")


async def test_valid_pdf_whose_renderer_times_out_is_an_engine_failure() -> None:
    def _timeout(_p: Any) -> int:
        raise PDFPopplerTimeoutError("pdfinfo timed out")

    result = await _extract_pdf(valid_pdf(), count=_timeout)
    assert result.failure_kind == "engine_failed"
    assert "timed out" in (result.degradation_reason or "")


async def test_every_page_failing_is_an_engine_failure() -> None:
    async def _fails(_engine: Any, _img: Any, **_: Any) -> tuple[str, float, str]:
        _note_page_failure("LLM vision OCR failed (TimeoutError)")
        return "", 0.0, "llm_vision"

    with (
        patch("app.ocr.engine.pdf_page_count", lambda _p: 2),
        patch("app.ocr.engine.render_pdf_page_image", lambda _p, n, *, dpi: page_image(n)),
        patch.object(OcrEngine, "_ocr_page", _fails),
    ):
        result = await OcrEngine().extract(pdf_bytes=valid_pdf(2), extract_fields=False)
    assert result.page_count == 2
    assert result.failed_pages == [1, 2]
    assert result.failure_kind == "engine_failed"


# ── readable documents are unchanged ─────────────────────────────────────────


async def test_valid_pdf_reads_normally() -> None:
    result = await _extract_pdf(valid_pdf(), count=lambda _p: 3)
    assert result.page_count == 3
    assert result.raw_text == "page 1 text\n\npage 2 text\n\npage 3 text"
    assert result.degraded is False
    assert result.failure_kind is None


async def test_owner_password_only_pdf_reads_normally() -> None:
    result = await _extract_pdf(encrypted_pdf(user_password=""), count=lambda _p: 3)
    assert result.page_count == 3
    assert result.failure_kind is None and result.degraded is False


# ── a damaged PDF the renderer partly recovered ─────────────────────────────


async def test_damaged_pdf_with_some_pages_unreadable_is_degraded_with_the_damage() -> None:
    def _render(_p: Any, n: int, *, dpi: int) -> Any:
        if n == 2:
            raise RuntimeError("pdftoppm: page 2 is damaged")
        return page_image(n)

    result = await _extract_pdf(truncated_pdf(), count=lambda _p: 2, render=_render)
    assert result.page_count == 2
    assert result.failed_pages == [2]
    assert result.degraded is True
    assert result.failure_kind is None  # page 1 was read: a partial answer
    assert (result.degradation_reason or "").startswith("the PDF is corrupt or truncated")


async def test_damaged_pdf_whose_recovered_pages_have_no_text_is_invalid_input() -> None:
    async def _blank(_engine: Any, _img: Any, **_: Any) -> tuple[str, float, str]:
        return "", 0.0, "tesseract"

    with (
        patch("app.ocr.engine.pdf_page_count", lambda _p: 2),
        patch("app.ocr.engine.render_pdf_page_image", lambda _p, n, *, dpi: page_image(n)),
        patch.object(OcrEngine, "_ocr_page", _blank),
    ):
        result = await OcrEngine().extract(pdf_bytes=truncated_pdf(), extract_fields=False)
    assert result.failure_kind == "invalid_input"
    assert "no text could be read" in (result.degradation_reason or "")


async def test_damage_is_not_reported_when_every_recovered_page_was_read() -> None:
    result = await _extract_pdf(truncated_pdf(), count=lambda _p: 2)
    assert result.raw_text == "page 1 text\n\npage 2 text"
    assert result.degraded is False and result.failure_kind is None


# ── images ───────────────────────────────────────────────────────────────────


async def test_undecodable_image_is_invalid_input() -> None:
    result = await OcrEngine().extract(image_bytes=b"\x89PNG\r\n\x1a\nbroken", extract_fields=False)
    assert result.page_count == 0
    assert result.failure_kind == "invalid_input"
    assert "image could not be decoded" in (result.degradation_reason or "")


async def test_no_input_at_all_stays_a_plain_empty_result() -> None:
    result = await OcrEngine().extract()
    assert result.page_count == 0 and result.failure_kind is None


# ── the real renderer, where poppler + pdf2image are installed (the image) ───


def _have_poppler() -> bool:
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        return False
    return shutil.which("pdfinfo") is not None


@pytest.mark.skipif(not _have_poppler(), reason="needs pdf2image and poppler (pdfinfo)")
@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (corrupt_pdf(), "invalid_input"),
        (truncated_pdf(), "invalid_input"),
        (encrypted_pdf(), "invalid_input"),
        (zero_page_pdf(), "invalid_input"),
        (valid_pdf(), None),
    ],
    ids=["corrupt", "truncated", "encrypted", "zero-pages", "valid"],
)
async def test_real_renderer_classification(data: bytes, kind: str | None) -> None:
    with patch.object(OcrEngine, "_ocr_page", _ocr_text):
        result = await OcrEngine().extract(pdf_bytes=data, extract_fields=False)
    assert result.failure_kind == kind
    assert result.page_count == (3 if kind is None else 0)
