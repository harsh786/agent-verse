"""OcrEngine picks the OCR language and page orientation per page (P1a-2).

Live P1a: every page was OCR'd as ``hin+eng`` first. On a Latin-script receipt
photo the Devanagari model turned digits into other digits with high confidence
(``TJ-5531`` -> ``TJ-5534``, ``INR 1,86,000`` -> ``INR 7,86,000``), so the
wrong job number was indexed and served. ``eng`` read it correctly. A page
photographed sideways OCR'd to garbage (only an LLM-vision fallback saved it).

Now: ``eng`` first; ``hin+eng`` only when the page looks Devanagari (OSD script,
or a weak English pass) and is kept only when its text really is Devanagari; a
low-confidence page is re-run after OSD orientation correction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from PIL import Image

from app.ocr.engine import OcrEngine


def _data(text: str, conf: int) -> dict[str, list[Any]]:
    words = text.split()
    return {"text": words, "conf": [conf] * len(words)}


class _FakeTess:
    """pytesseract stand-in: results keyed by (language, orientation)."""

    TesseractError = RuntimeError

    def __init__(self, results: dict[tuple[str, str], dict[str, list[Any]]],
                 osd: dict[str, Any] | None = None, langs: tuple[str, ...] = ("eng", "hin",
                                                                            "osd")) -> None:
        self.results = results
        self.osd = osd
        self.langs = langs
        self.calls: list[tuple[str, str]] = []
        self.Output = MagicMock(DICT="dict")

    def get_languages(self, config: str = "") -> list[str]:
        return list(self.langs)

    def image_to_data(self, img: Any, lang: str = "eng", output_type: Any = None) -> Any:
        orient = "portrait" if img.height > img.width else "landscape"
        self.calls.append((lang, orient))
        return self.results.get((lang, orient), _data("", -1))

    def image_to_osd(self, img: Any, output_type: Any = None) -> dict[str, Any]:
        if self.osd is None:
            raise RuntimeError("Too few characters. Skipping this page")
        return self.osd


async def _ocr(tess: _FakeTess, size: tuple[int, int] = (700, 450)) -> tuple[str, float, str]:
    engine = OcrEngine()
    with patch.dict("sys.modules", {"pytesseract": tess}):
        return await engine._ocr_page(Image.new("L", size, 255), vision_fallback=False)


async def test_latin_page_is_read_with_english_not_the_devanagari_model() -> None:
    tess = _FakeTess({("eng", "landscape"): _data("Towage job: TJ-5531", 91),
                      ("hin+eng", "landscape"): _data("Towage job: TJ-5534", 93)})
    text, conf, engine = await _ocr(tess)
    assert text == "Towage job: TJ-5531"
    assert engine == "tesseract" and conf > 0.9
    assert [lang for lang, _ in tess.calls] == ["eng"]


async def test_weak_latin_page_keeps_english_when_hindi_finds_no_devanagari() -> None:
    tess = _FakeTess({("eng", "landscape"): _data("Amount: INR 1,86,000", 72),
                      ("hin+eng", "landscape"): _data("Amount: INR 7,86,000", 78)})
    text, _, _ = await _ocr(tess)
    assert text == "Amount: INR 1,86,000"


async def test_hindi_page_uses_the_devanagari_model() -> None:
    hindi = "रात्रि पाली का भत्ता 850 रुपये"
    tess = _FakeTess({("eng", "landscape"): _data("Tal Al Gel 850", 41),
                      ("hin+eng", "landscape"): _data(hindi, 77)})
    text, _, _ = await _ocr(tess)
    assert text == hindi


async def test_confident_english_page_skips_osd_and_the_hindi_pass() -> None:
    tess = _FakeTess({("eng", "landscape"): _data("Pass no GP-77120", 92)},
                     osd={"rotate": 90, "orientation_conf": 9.0, "script": "Devanagari",
                          "script_conf": 9.0})
    text, _, _ = await _ocr(tess)
    assert text == "Pass no GP-77120"
    assert tess.calls == [("eng", "landscape")]


async def test_sideways_page_is_rotated_by_osd_and_re_read() -> None:
    tess = _FakeTess({("eng", "portrait"): _data("SSVd ALVD SLYOd", 40),
                      ("eng", "landscape"): _data("GATE PASS GP-77120 Driver Tomasz Wieczorek",
                                                  90)},
                     osd={"rotate": 90, "orientation_conf": 3.2, "script": "Latin",
                          "script_conf": 6.0})
    text, conf, _ = await _ocr(tess, size=(900, 1400))
    assert text == "GATE PASS GP-77120 Driver Tomasz Wieczorek"
    assert conf > 0.85


async def test_low_confidence_osd_rotation_is_ignored() -> None:
    tess = _FakeTess({("eng", "portrait"): _data("faint words", 45)},
                     osd={"rotate": 90, "orientation_conf": 0.4, "script": "Latin",
                          "script_conf": 1.0})
    text, _, _ = await _ocr(tess, size=(900, 1400))
    assert text == "faint words"
    assert all(o == "portrait" for _, o in tess.calls)


async def test_without_hindi_traineddata_only_english_runs() -> None:
    tess = _FakeTess({("eng", "landscape"): _data("weak text", 50)}, langs=("eng",))
    text, _, _ = await _ocr(tess)
    assert text == "weak text"
    assert {lang for lang, _ in tess.calls} == {"eng"}
