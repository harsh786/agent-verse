"""OCR fallback gaps (OCR-FB-1..3).

1. A page Tesseract read with low confidence keeps that text when LLM vision then
   fails, instead of becoming an empty page.
2. LLM-vision OCR fails over to the other configured vision/OCR models.
3. ``engine_used`` / confidence describe every page honestly: "mixed" engines,
   vision pages counted, and the unmeasured vision confidence kept out of the
   measured average.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from app.ocr import engine as engine_mod
from app.ocr.engine import OcrEngine, _summarise_pages


def _img() -> Image.Image:
    return Image.new("L", (64, 32), 255)


@pytest.fixture(autouse=True)
def _pytesseract_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_ocr_page`` imports pytesseract (an optional extra) before reading; the
    readings themselves are patched, so a stand-in module is enough."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "pytesseract", types.ModuleType("pytesseract"))


# ── 1. vision failure keeps the low-confidence Tesseract text ────────────────


async def _tesseract_low(self: Any, _pyt: Any, _img: Any) -> tuple[str, float]:
    return "faint invoice INV-42", 0.41


@pytest.mark.asyncio
async def test_vision_failure_keeps_the_low_confidence_tesseract_text() -> None:
    async def _vision_fails(self: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        return "", 0.0, "llm_vision"

    with (
        patch.object(OcrEngine, "_tesseract_best", _tesseract_low),
        patch.object(OcrEngine, "_llm_vision_ocr", _vision_fails),
    ):
        text, conf, engine = await OcrEngine()._ocr_page(_img())

    assert text == "faint invoice INV-42"
    assert conf == pytest.approx(0.41)
    assert engine == "tesseract"


@pytest.mark.asyncio
async def test_vision_success_still_replaces_low_confidence_tesseract_text() -> None:
    async def _vision_ok(self: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        return "Invoice INV-42 total 1,180.00", 0.85, "llm_vision"

    with (
        patch.object(OcrEngine, "_tesseract_best", _tesseract_low),
        patch.object(OcrEngine, "_llm_vision_ocr", _vision_ok),
    ):
        text, _conf, engine = await OcrEngine()._ocr_page(_img())

    assert text == "Invoice INV-42 total 1,180.00"
    assert engine == "llm_vision"


@pytest.mark.asyncio
async def test_no_tesseract_text_and_vision_failure_is_an_empty_vision_page() -> None:
    async def _tesseract_none(self: Any, _p: Any, _i: Any) -> tuple[str, float]:
        return "", 0.0

    async def _vision_fails(self: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        return "", 0.0, "llm_vision"

    with (
        patch.object(OcrEngine, "_tesseract_best", _tesseract_none),
        patch.object(OcrEngine, "_llm_vision_ocr", _vision_fails),
    ):
        assert await OcrEngine()._ocr_page(_img()) == ("", 0.0, "llm_vision")


# ── 2. vision model failover ─────────────────────────────────────────────────


@dataclass
class _Resp:
    content: str
    model: str


class _RoutingProvider:
    """Answers per model: the primary is down, the fallback answers."""

    def __init__(self, broken: set[str]) -> None:
        self.broken = broken
        self.models_called: list[str] = []

    async def complete(self, request: Any) -> _Resp:
        self.models_called.append(request.model)
        if request.model in self.broken:
            raise RuntimeError(f"{request.model} is down")
        return _Resp(content=f"text read by {request.model}", model=request.model)


@pytest.mark.asyncio
async def test_vision_ocr_fails_over_to_the_next_configured_vision_model() -> None:
    provider = _RoutingProvider(broken={"vision-primary"})
    with (
        patch.object(engine_mod, "_ocr_model", lambda: "vision-primary"),
        patch.object(engine_mod, "_ocr_fallback_models", lambda primary: ["vision-backup"]),
        patch("app.providers.guarded_completion._require_attribution", lambda *a: None),
        patch("app.providers.guarded_completion._preflight", _noop),
        patch("app.providers.guarded_completion._charge", _noop),
    ):
        text, _conf, engine = await OcrEngine()._llm_vision_ocr(_img(), provider=provider)

    assert text == "text read by vision-backup"
    assert engine == "llm_vision"
    assert provider.models_called[0] == "vision-primary"
    assert provider.models_called[-1] == "vision-backup"


async def _noop(*_a: Any, **_k: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_failover_charges_the_model_that_answered() -> None:
    from app.providers.guarded_completion import complete_decision
    from app.providers.base import CompletionRequest, Message

    charged: list[str] = []

    async def _charge(*_a: Any, model: str, **_k: Any) -> None:
        charged.append(model)

    provider = _RoutingProvider(broken={"vision-primary"})
    req = CompletionRequest(messages=[Message(role="user", content="x")], model="vision-primary")
    with (
        patch("app.providers.guarded_completion._require_attribution", lambda *a: None),
        patch("app.providers.guarded_completion._preflight", _noop),
        patch("app.providers.guarded_completion._charge", _charge),
    ):
        resp = await complete_decision(
            provider, req, role="ocr_vision", tenant_id="t1", fallback_models=["vision-backup"]
        )

    assert resp.model == "vision-backup"
    assert charged == ["vision-backup"]


def test_vision_fallback_list_skips_the_primary_and_duplicates() -> None:
    from app.ai_router import selection

    @dataclass
    class _M:
        model_id: str
        cost_per_1k_input: float
        quality_score: float = 0.8
        avg_latency_ms: float = 100.0
        supports_vision: bool = True

    class _Reg:
        def list_configured(self, capability: Any) -> list[_M]:
            return [_M("cheap-vlm", 0.1), _M("vision-primary", 0.2), _M("big-vlm", 0.9)]

    with (
        patch.object(selection, "model_registry", _Reg()),
        patch.object(selection, "_ensure_seeded", lambda reg: None),
        patch("app.providers.model_defaults.configured_vision_model", lambda f="": "env-vlm"),
    ):
        out = selection.resolve_vision_fallback_models("vision-primary")

    assert "vision-primary" not in out
    assert out[0] == "cheap-vlm"
    assert len(out) == len(set(out))


# ── 3. honest per-page reporting ─────────────────────────────────────────────


def test_mixed_pages_report_mixed_and_average_only_measured_confidence() -> None:
    pages = [("page one", 0.9, "tesseract"), ("page two", 0.85, "llm_vision")]
    overall, engine, vision_pages, measured = _summarise_pages(pages)
    assert engine == "mixed"
    assert vision_pages == 1
    assert measured is True
    assert overall == pytest.approx(0.9)  # the vision page's assumed score is not averaged


def test_all_vision_pages_report_the_assumption_as_unmeasured() -> None:
    overall, engine, vision_pages, measured = _summarise_pages(
        [("a", 0.85, "llm_vision"), ("b", 0.85, "llm_vision")]
    )
    assert engine == "llm_vision"
    assert vision_pages == 2
    assert measured is False
    assert overall == pytest.approx(engine_mod.VISION_ASSUMED_CONFIDENCE)


def test_an_empty_vision_page_counts_as_a_measured_zero() -> None:
    overall, _engine, vision_pages, measured = _summarise_pages(
        [("text", 0.8, "tesseract"), ("", 0.0, "llm_vision")]
    )
    assert vision_pages == 0
    assert measured is True
    assert overall == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_extract_records_per_page_engines_on_the_result() -> None:
    results = iter([("page one", 0.9, "tesseract"), ("page two", 0.85, "llm_vision")])

    async def _page(self: Any, img: Any, **_: Any) -> tuple[str, float, str]:
        return next(results)

    async def _open(self: Any, **_: Any) -> Any:
        return None

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _pages(self: Any, **_: Any) -> Any:
        yield [lambda: _img(), lambda: _img()]

    with (
        patch.object(OcrEngine, "_open_pages", _pages),
        patch.object(OcrEngine, "_ocr_page", _page),
    ):
        result = await OcrEngine().extract(image_bytes=b"x", extract_fields=False)

    assert result.engine_used == "mixed"
    assert result.page_engines == ["tesseract", "llm_vision"]
    assert result.vision_pages == 1
    assert result.confidence_measured is True
    assert result.overall_confidence == pytest.approx(0.9)
