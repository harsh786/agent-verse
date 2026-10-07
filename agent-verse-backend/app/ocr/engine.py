"""OCR engine: Tesseract primary with LLM vision fallback."""

from __future__ import annotations

import base64
import io
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.ocr.classifier import DocumentClassifier
from app.ocr.concurrency import (
    current_limits,
    map_bounded,
    ocr_document_scope,
    ocr_page_slot,
    ocr_vision_slot,
    run_ocr_work,
)
from app.ocr.extractors import get_extractor
from app.ocr.models import DocumentType, OcrFailureKind, OcrResult
from app.ocr.rasterize import (
    describe_render_failure,
    pdf_page_count,
    pdf_structure_problem,
    render_pdf_page_image,
)

# A page to OCR: a blocking zero-argument loader (decode / rasterise) that returns
# the page's PIL image, run on the OCR pool when the page gets its slot.
PageLoader = Callable[[], Any]

_log = logging.getLogger(__name__)

# a10-F243-03: why the page being OCR'd in this task produced no text. Each page
# runs in its own task (map_bounded), so the sink set there is that page's own;
# callers outside a document extraction (sink None) record nothing.
_PAGE_FAILURES: ContextVar[list[str] | None] = ContextVar("ocr_page_failures", default=None)


def _note_page_failure(reason: str) -> None:
    sink = _PAGE_FAILURES.get()
    if sink is not None:
        sink.append(reason)


def _page_outcome(
    results: Sequence[tuple[tuple[str, float, str], list[str]]],
) -> tuple[list[int], list[int], str | None]:
    """``(empty_pages, failed_pages, degradation_reason)`` of a document's pages.

    A page is *failed* when it has no text and the engine recorded a failure on
    it; a blank page the engine read successfully is only *empty*. The reason is
    set when any page failed, naming the distinct failure causes.
    """
    empty = [i for i, ((text, _, _), _) in enumerate(results, 1) if not text.strip()]
    failed = [i for i, ((text, _, _), why) in enumerate(results, 1) if not text.strip() and why]
    if not failed:
        return empty, failed, None
    causes = "; ".join(dict.fromkeys(r for _, why in results for r in why))
    total = len(results)
    if len(failed) == total:
        reason = f"no text could be read from any of the {total} page(s): {causes}"
    else:
        listed = ", ".join(str(n) for n in failed[:20]) + (", ..." if len(failed) > 20 else "")
        reason = f"{len(failed)} of {total} page(s) could not be read (pages {listed}): {causes}"
    return empty, failed, reason


@dataclass
class _InputReport:
    """What opening the input found (filled in by ``OcrEngine._open_pages``).

    ``failure_kind`` / ``reason``: why the input yielded NO pages at all.
    ``pdf_bytes``: the PDF the renderer did yield pages for — checked for
    structural damage (pypdf) only if those pages come out unreadable, so a
    readable document never pays for the second parse.
    """

    failure_kind: OcrFailureKind | None = None
    reason: str | None = None
    pdf_bytes: bytes | None = None


def _decode_error(img: Any) -> str | None:
    """Decode a lazily-opened PIL image now; why it cannot be decoded, else None."""
    try:
        img.load()
    except Exception as exc:
        return f"{type(exc).__name__}: {' '.join(str(exc).split())[:120]}"
    return None


def _pdf_problem_or_none(data: bytes) -> tuple[str, str] | None:
    try:
        return pdf_structure_problem(data)
    except ImportError:
        return None  # no pypdf: the renderer's verdict alone decides


def _ocr_model() -> str:
    """The configured OCR/vision model (independent of the reasoning model).

    :func:`app.ai_router.resolve.resolve_ocr` without its local tier: the
    Model Registry's OCR order, then its vision models, then ``OCR_MODEL`` —
    never the reasoning model. Empty when none is configured (the provider
    keeps its own default; Tesseract is the engine's own tier).
    """
    from app.ai_router.selection import resolve_ocr_model

    return resolve_ocr_model("")


def _ocr_fallback_models(primary: str) -> list[str]:
    """Other configured OCR/vision models in preference order (else cheapest
    first), tried in turn when the primary fails (down, timing out, empty answer)."""
    from app.ai_router.selection import resolve_ocr_fallback_models

    return resolve_ocr_fallback_models(primary)


def _summarise_pages(pages: list[tuple[str, float, str]]) -> tuple[float, str, int, bool]:
    """``(overall_confidence, engine_used, vision_pages, confidence_measured)``.

    Only measured confidences are averaged: Tesseract pages, plus pages that
    produced no text at all (a measured 0). A page LLM vision did read has no
    real score, so it is counted in ``vision_pages`` instead of skewing the
    average with a constant. When nothing was measured, the configured assumed
    confidence is reported and ``confidence_measured`` is False.
    """
    engines = {e for _, _, e in pages}
    engine_used = engines.pop() if len(engines) == 1 else "mixed"
    vision_pages = sum(1 for t, _, e in pages if e == "llm_vision" and t.strip())
    measured = [c for t, c, e in pages if e != "llm_vision" or not t.strip()]
    if measured:
        return sum(measured) / len(measured), engine_used, vision_pages, True
    return (VISION_ASSUMED_CONFIDENCE if vision_pages else 0.0), engine_used, vision_pages, False

CONFIDENCE_THRESHOLD = 0.6


def tesseract_enabled() -> bool:
    """``OCR_TESSERACT_ENABLED`` (default OFF, owner decision 2026-10-06).

    Off: Tesseract is skipped and every page goes straight to the LLM-vision OCR
    model (the Model Registry's OCR order, with failover). ``true`` restores
    Tesseract first, vision only for low-confidence pages. Read per call, so
    tests and operators can toggle it."""
    raw = os.getenv("OCR_TESSERACT_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# Vision cost reservation (OCR-VISION-BUDGET): the most one page's vision call
# may cost. Image tokens are estimated at ~750 pixels per token (the common
# provider rule), never below a small image's floor nor above a large one's cap
# (providers downscale big images), plus the fixed instruction text.
_VISION_PIXELS_PER_TOKEN = 750
_VISION_MIN_IMAGE_TOKENS = 258
_VISION_MAX_IMAGE_TOKENS = 6_400
_VISION_PROMPT_TOKENS = 64


def vision_reserve_usd(img: Any, models: Sequence[str], *, max_output_tokens: int) -> float:
    """Worst-case USD cost of one vision OCR call: the page image plus the full
    output allowance, at the priciest of the models that may answer (failover)."""
    from app.intelligence.cost_tracker import calculate_cost

    try:
        width, height = (int(v) for v in img.size)
    except Exception:
        width = height = 0
    image_tokens = min(
        _VISION_MAX_IMAGE_TOKENS,
        max(_VISION_MIN_IMAGE_TOKENS, -(-width * height // _VISION_PIXELS_PER_TOKEN)),
    )
    prompt_tokens = image_tokens + _VISION_PROMPT_TOKENS
    output_tokens = max(1, int(max_output_tokens or 0))
    costs: list[float] = []
    for model in list(dict.fromkeys(str(m or "") for m in models)) or [""]:
        try:
            costs.append(float(calculate_cost(model, prompt_tokens, output_tokens)))
        except Exception:
            continue
    return max(costs, default=0.0)


# LLM vision gives no confidence score. A page it read is reported with this
# assumed value and excluded from the measured average (see _summarise_pages).
VISION_ASSUMED_CONFIDENCE = float(os.getenv("OCR_VISION_ASSUMED_CONFIDENCE", "") or 0.85)
# Below this English confidence a page may be Hindi: the hin+eng model is tried.
HINDI_RETRY_CONFIDENCE = 0.85
# Share of letters that must be Devanagari for the hin+eng reading to be kept.
DEVANAGARI_MIN_SHARE = 0.15
# OSD orientation confidence needed before a page is rotated.
OSD_MIN_CONFIDENCE = 1.5


def _tesseract_text_conf(data: Any) -> tuple[str, float]:
    """Text and mean word confidence (0-1) of a pytesseract ``image_to_data`` dict."""
    pairs = [
        (str(t), float(c))
        for t, c in zip(data.get("text", []), data.get("conf", []), strict=False)
        if isinstance(c, (int, float)) and c >= 0
    ]
    confs = [c for _, c in pairs]
    text = " ".join(t for t, _ in pairs if t.strip())
    return text, (sum(confs) / len(confs) / 100.0) if confs else 0.0


def _tesseract_osd(pytesseract: Any, img: Any) -> dict[str, Any] | None:
    """Orientation / script detection, or None (no osd data, too little text)."""
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
    except Exception:
        return None
    return osd if isinstance(osd, dict) else None


def _tesseract_languages(pytesseract: Any) -> set[str]:
    try:
        langs = pytesseract.get_languages(config="")
    except Exception:
        return set()
    return {str(lang) for lang in langs} if isinstance(langs, (list, tuple, set)) else set()


def _devanagari_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "\u0900" <= ch <= "\u097f") / len(letters)

OcrFormat = Literal["image", "pdf", "office", "text", "unsupported"]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods", ".rtf"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".html", ".htm", ".xml"}


def detect_ocr_format(
    data: bytes, content_type: str | None, filename: str | None
) -> OcrFormat:
    """Classify an arbitrary input as an OCR input class.

    Precedence: content-type → filename extension → magic bytes. Office formats
    are zip containers (``PK\\x03\\x04``) so they are only recognised via the
    content-type/extension, never magic alone.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = Path(filename).suffix.lower() if filename else ""

    if ct.startswith("image/") or ext in _IMAGE_EXTS:
        return "image"
    if ct == "application/pdf" or ext == ".pdf":
        return "pdf"
    office_cts = ("officedocument", "msword", "ms-excel", "ms-powerpoint", "opendocument")
    if any(tok in ct for tok in office_cts) or ext in _OFFICE_EXTS:
        return "office"
    if ct.startswith("text/") or ext in _TEXT_EXTS:
        return "text"

    # Magic-byte fallback for callers that provide neither content-type nor name.
    if data[:5] == b"%PDF-":
        return "pdf"
    if (
        data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:3] == b"\xff\xd8\xff"  # JPEG
        or data[:6] in (b"GIF87a", b"GIF89a")
        or data[:2] == b"BM"  # BMP
        or data[:4] in (b"II*\x00", b"MM\x00*")  # TIFF
    ):
        return "image"
    return "unsupported"


class OcrEngine:
    """Extract text and structured fields from images and PDFs.

    Primary: Tesseract (local, free, offline).
    Fallback: LLM vision via existing app.providers when Tesseract is
    unavailable or returns low confidence.
    """

    def __init__(self) -> None:
        self._classifier = DocumentClassifier()
        self._fallback_provider: Any = None

    async def extract(
        self,
        *,
        image_bytes: bytes | None = None,
        pdf_bytes: bytes | None = None,
        provider: Any = None,
        extract_fields: bool = True,
        vision_fallback: bool = True,
    ) -> OcrResult:
        """Extract text and structured fields from an image or PDF.

        ``extract_fields=False`` returns the OCR text only (no classification /
        field extraction — no extra LLM call when the caller just indexes text).
        ``vision_fallback=False`` never falls back to LLM vision: low-confidence
        Tesseract text is returned as-is (the caller has no vision-capable
        provider, and the system default one might return canned text).
        """
        # One fairness queue per document (OCR-FAIR): its pages share the
        # process-wide slots round-robin with other documents and tenants.
        with ocr_document_scope():
            return await self._extract(
                image_bytes=image_bytes,
                pdf_bytes=pdf_bytes,
                provider=provider,
                extract_fields=extract_fields,
                vision_fallback=vision_fallback,
            )

    async def _extract(
        self,
        *,
        image_bytes: bytes | None,
        pdf_bytes: bytes | None,
        provider: Any,
        extract_fields: bool,
        vision_fallback: bool,
    ) -> OcrResult:
        report = _InputReport()
        async with self._open_pages(
            image_bytes=image_bytes, pdf_bytes=pdf_bytes, report=report
        ) as pages:
            if not pages:
                # An input that yielded no page is never a silent empty answer:
                # it carries why (invalid input vs. renderer failure).
                if report.failure_kind is not None:
                    _log.warning(
                        "ocr_input_unreadable kind=%s reason=%s",
                        report.failure_kind,
                        report.reason,
                    )
                return OcrResult(
                    raw_text="",
                    document_type=DocumentType.GENERAL,
                    overall_confidence=0.0,
                    page_count=0,
                    degraded=report.failure_kind is not None,
                    degradation_reason=report.reason,
                    failure_kind=report.failure_kind,
                )

            async def _page(load: PageLoader) -> tuple[tuple[str, float, str], list[str]]:
                # This page's task-local failure sink (see _PAGE_FAILURES).
                failures: list[str] = []
                _PAGE_FAILURES.set(failures)
                page = await self._ocr_loaded_page(
                    load, provider=provider, vision_fallback=vision_fallback
                )
                return page, failures

            # Pages run concurrently (bounded per document and process-wide);
            # results stay in page order, so each page keeps its own text.
            page_results = await map_bounded(
                pages, _page, limit=current_limits().page_concurrency
            )

        raw_texts = [page for page, _ in page_results]
        raw_text = "\n\n".join(t for t, _, _ in raw_texts)
        overall_conf, engine_used, vision_pages, measured = _summarise_pages(raw_texts)
        # a10-F243-03: pages the engine failed on (no provider, vision error,
        # render failure) used to be joined in as silent empty text.
        empty_pages, failed_pages, failure_reason = _page_outcome(page_results)
        failure_kind: OcrFailureKind | None = None
        damage: str | None = None
        if report.pdf_bytes is not None and (failed_pages or len(empty_pages) == len(pages)):
            problem = await run_ocr_work(_pdf_problem_or_none, report.pdf_bytes)
            if problem is not None and problem[0] == "damaged":
                damage = problem[1]
        if damage:
            # Pages the renderer recovered from a damaged PDF came out unreadable:
            # the damage is the cause, and a document with no text is bad input.
            if len(empty_pages) == len(pages):
                failure_kind = "invalid_input"
                failure_reason = (
                    f"{damage}; no text could be read from the {len(pages)} "
                    "page(s) the renderer recovered"
                )
            else:
                failure_reason = f"{damage}; {failure_reason}"
        elif failed_pages and len(failed_pages) == len(pages):
            failure_kind = "engine_failed"
        if failure_reason:
            _log.warning("ocr_pages_failed %s", failure_reason)
        provenance: dict[str, Any] = {
            "page_engines": [e for _, _, e in raw_texts],
            "vision_pages": vision_pages,
            "confidence_measured": measured,
            "empty_pages": empty_pages,
            "failed_pages": failed_pages,
            "degraded": failure_reason is not None,
            "degradation_reason": failure_reason,
            "failure_kind": failure_kind,
        }

        if not extract_fields:
            return OcrResult(
                raw_text=raw_text,
                document_type=DocumentType.GENERAL,
                engine_used=engine_used,  # type: ignore[arg-type]
                overall_confidence=overall_conf,
                page_count=len(pages),
                **provenance,
            )

        doc_type = self._classifier.classify(raw_text)
        extractor = get_extractor(doc_type, provider=provider)

        # For LLM-structured extractor, call async method
        from app.ocr.extractors.general import LlmStructuredExtractor

        if isinstance(extractor, LlmStructuredExtractor):
            fields = await extractor.extract_async(raw_text)
        else:
            fields = extractor.extract(raw_text)

        return OcrResult(
            raw_text=raw_text,
            document_type=doc_type,
            fields=fields,
            engine_used=engine_used,  # type: ignore[arg-type]
            overall_confidence=overall_conf,
            page_count=len(pages),
            **provenance,
        )

    async def extract_any(
        self,
        data: bytes,
        *,
        content_type: str | None = None,
        filename: str | None = None,
        provider: Any = None,
    ) -> OcrResult:
        """Universal entry point: OCR text out of ANY input format (WS-6).

        Routes by detected format — images and PDFs rasterize directly; office
        documents are converted to PDF via LibreOffice (``soffice``) when it is
        available, then rasterized. Formats that cannot be rasterized (native
        text, unknown binaries, office docs with no converter) return an honest
        degraded result recording *why*, never a silent empty drop. The detected
        ``source_format`` is always recorded on the result.
        """
        fmt = detect_ocr_format(data, content_type, filename)

        if fmt == "image":
            return self._annotate(
                await self.extract(image_bytes=data, provider=provider), source_format="image"
            )
        if fmt == "pdf":
            return self._annotate(
                await self.extract(pdf_bytes=data, provider=provider), source_format="pdf"
            )
        if fmt == "office":
            # LibreOffice runs up to 120 s: on the OCR pool, never on the event loop.
            pdf_bytes = await run_ocr_work(self._office_to_pdf, data, filename)
            if pdf_bytes is None:
                return self._degraded(
                    "office",
                    "office document conversion requires LibreOffice (soffice), "
                    "which is not available on this host",
                )
            return self._annotate(
                await self.extract(pdf_bytes=pdf_bytes, provider=provider), source_format="office"
            )
        if fmt == "text":
            return self._degraded(
                "text", "input is already machine-readable text; OCR is not applicable"
            )
        return self._degraded(
            "unsupported",
            f"unsupported format for OCR (content_type={content_type!r}, filename={filename!r})",
        )

    @staticmethod
    def _annotate(result: OcrResult, *, source_format: str) -> OcrResult:
        """Record the source format and flag a rasterization that yielded no pages."""
        result.source_format = source_format
        if result.page_count == 0 and not result.degraded:
            result.degraded = True
            result.degradation_reason = (
                f"could not rasterize {source_format} input to images "
                "(missing renderer, e.g. poppler/pdf2image or Pillow?)"
            )
        return result

    @staticmethod
    def _degraded(source_format: str, reason: str) -> OcrResult:
        _log.info("ocr_degraded format=%s reason=%s", source_format, reason)
        return OcrResult(
            raw_text="",
            document_type=DocumentType.GENERAL,
            overall_confidence=0.0,
            page_count=0,
            degraded=True,
            degradation_reason=reason,
            source_format=source_format,
        )

    @staticmethod
    def _office_to_pdf(data: bytes, filename: str | None) -> bytes | None:
        """Convert an office document to PDF bytes via LibreOffice headless.

        Returns ``None`` when no converter (``soffice``/``libreoffice``) is on
        PATH, or on any conversion failure — the caller degrades honestly.
        """
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            return None
        suffix = Path(filename).suffix if filename else ".docx"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"input{suffix}"
                src.write_bytes(data)
                # Its own LibreOffice profile: concurrent conversions sharing the
                # default profile lock each other out (the second one fails).
                profile = (Path(tmp) / "lo-profile").as_uri()
                subprocess.run(
                    [
                        soffice,
                        f"-env:UserInstallation={profile}",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        tmp,
                        str(src),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                out = src.with_suffix(".pdf")
                if out.exists():
                    return out.read_bytes()
        except Exception as exc:
            _log.warning("office_to_pdf failed: %s", exc)
        return None

    def _to_images(self, *, image_bytes: bytes | None) -> list[Any]:
        """The (lazily decoded) PIL image of an image input, else ``[]``."""
        if image_bytes:
            try:
                from PIL import Image

                img: Any = Image.open(io.BytesIO(image_bytes))
                return [img]
            except Exception as exc:
                _log.warning("Failed to open image bytes: %s", exc)
                return []
        return []

    @asynccontextmanager
    async def _open_pages(
        self,
        *,
        image_bytes: bytes | None,
        pdf_bytes: bytes | None,
        report: _InputReport | None = None,
    ) -> AsyncIterator[list[PageLoader]]:
        """One loader per page of the input, in page order (``[]``: no pages).

        A PDF is written to a temporary file ONCE and each loader renders one
        page from it (``first_page == last_page``) when that page gets its slot,
        so a 200-page scan never holds every page bitmap at once; the page count
        and every render run on the OCR pool, never on the event loop.

        When the input yields no pages, ``report`` records why: ``invalid_input``
        when the document itself cannot be opened (corrupt, truncated, encrypted,
        no pages, not an image — confirmed by pypdf, independently of poppler),
        ``engine_failed`` when the renderer failed on (or is missing for) a
        document pypdf can read. This used to be a silent zero-page result.
        """
        report = report if report is not None else _InputReport()
        if image_bytes:
            images = self._to_images(image_bytes=image_bytes)
            # PIL decodes lazily: a truncated image opens fine and only fails
            # later (its page was then sent to vision OCR with no image data).
            undecodable = await run_ocr_work(_decode_error, images[0]) if images else None
            if not images or undecodable:
                report.failure_kind = "invalid_input"
                report.reason = (
                    "the image could not be decoded (corrupt, truncated or not a "
                    "supported image format"
                    + (f": {undecodable})" if undecodable else ")")
                )
                images = []
            yield [(lambda img=img: img) for img in images]
            return
        if not pdf_bytes:
            yield []
            return
        with tempfile.TemporaryDirectory(prefix="ocr-pdf-") as tmp:
            path = Path(tmp) / "document.pdf"
            count = 0
            render_error: str | None = None
            try:
                await run_ocr_work(path.write_bytes, pdf_bytes)
                count = await run_ocr_work(pdf_page_count, path)
            except ImportError:
                render_error = "the PDF renderer (pdf2image/poppler) is not installed on this host"
                _log.warning("pdf2image not installed; cannot render PDF for OCR")
            except Exception as exc:
                render_error = describe_render_failure(exc)
                _log.warning("Failed to read PDF for OCR: %s", exc)
            if count <= 0:
                # Tell bad input from a failed renderer with an independent parser.
                problem = await run_ocr_work(_pdf_problem_or_none, pdf_bytes)
                if problem is not None:
                    report.failure_kind, report.reason = "invalid_input", problem[1]
                else:
                    report.failure_kind = "engine_failed"
                    report.reason = (
                        f"could not render the PDF: {render_error or 'the renderer found no pages'}"
                    )
                yield []
                return
            report.pdf_bytes = pdf_bytes
            dpi = current_limits().render_dpi
            yield [
                (lambda n=n: self._render_page(path, n, dpi)) for n in range(1, count + 1)
            ]

    @staticmethod
    def _render_page(path: Path, page_number: int, dpi: int) -> Any:
        """One rendered page, or None (that page is OCR'd as empty, the others
        keep their text)."""
        try:
            return render_pdf_page_image(path, page_number, dpi=dpi)
        except Exception as exc:
            _log.warning("Failed to render PDF page %d for OCR: %s", page_number, exc)
            return None

    async def _ocr_loaded_page(
        self, load: PageLoader, *, provider: Any, vision_fallback: bool
    ) -> tuple[str, float, str]:
        """Render / decode one page and OCR it, holding one process-wide page
        slot for as long as its bitmap is alive."""
        async with ocr_page_slot():
            img = await run_ocr_work(load)
            if img is None:
                _note_page_failure("page could not be rendered")
                return "", 0.0, "tesseract"
            return await self._ocr_page(img, provider=provider, vision_fallback=vision_fallback)

    async def _ocr_page(
        self,
        img: Any,
        *,
        provider: Any = None,
        vision_fallback: bool = True,
    ) -> tuple[str, float, str]:
        """Run OCR on a single page image. Returns (text, confidence, engine_name)."""
        low_conf: tuple[str, float, str] = ("", 0.0, "tesseract")
        if not tesseract_enabled():
            # OCR_TESSERACT_ENABLED=false: the vision model reads every page.
            if not vision_fallback:
                return low_conf
            return await self._llm_vision_ocr(img, provider=provider)
        try:
            import pytesseract

            plain = img
            img = await run_ocr_work(self._preprocess_image, img)
            text, avg_conf = await self._tesseract_best(pytesseract, img)
            if not text.strip():
                # Never trust preprocessing blindly: it once blacked out sparse pages.
                text, avg_conf = await self._tesseract_best(pytesseract, plain)
            if avg_conf >= CONFIDENCE_THRESHOLD:
                return text, avg_conf, "tesseract"
            low_conf = (text, avg_conf, "tesseract")

            _log.debug("Tesseract confidence %.2f below threshold; using LLM vision", avg_conf)
        except ImportError:
            _log.debug("pytesseract not installed; using LLM vision fallback")
        except Exception as exc:
            _log.warning("Tesseract failed: %s; using LLM vision fallback", exc)

        if not vision_fallback:
            return low_conf
        vision = await self._llm_vision_ocr(img, provider=provider)
        if not vision[0].strip() and low_conf[0].strip():
            # Vision failed or read nothing: the weaker Tesseract text is still
            # better than an empty page (it used to be dropped here).
            _log.info("ocr_vision_empty_kept_tesseract conf=%.2f", low_conf[1])
            return low_conf
        return vision

    async def _tesseract_best(self, pytesseract: Any, img: Any) -> tuple[str, float]:
        """The best Tesseract reading of one page: ``(text, mean confidence)``.

        English first. A weak page (below CONFIDENCE_THRESHOLD) is re-read after
        OSD orientation correction (a photo taken sideways). The Devanagari model
        (``hin+eng``) runs only when the English pass is below
        HINDI_RETRY_CONFIDENCE (the page may be Hindi) and is kept only when its
        text really is Devanagari: on Latin pages it misreads digits with high
        confidence (TJ-5531 -> TJ-5534).
        """
        # Every pass runs on the process-wide OCR pool (bounded by
        # OCR_MAX_CONCURRENCY), never on the shared default executor.
        def _read(image: Any, lang: str) -> tuple[str, float]:
            data = pytesseract.image_to_data(
                image, lang=lang, output_type=pytesseract.Output.DICT
            )
            return _tesseract_text_conf(data)

        def _rotate(image: Any, degrees: int) -> Any:
            return image.rotate(-degrees, expand=True, fillcolor=255)

        best = await run_ocr_work(_read, img, "eng")
        if best[1] >= HINDI_RETRY_CONFIDENCE:
            return best  # a confident English reading: no OSD / Hindi pass needed
        osd = await run_ocr_work(_tesseract_osd, pytesseract, img)
        if best[1] < CONFIDENCE_THRESHOLD and osd is not None:
            rotate = int(osd.get("rotate") or 0) % 360
            if rotate and float(osd.get("orientation_conf") or 0) >= OSD_MIN_CONFIDENCE:
                rotated = await run_ocr_work(_rotate, img, rotate)
                again = await run_ocr_work(_read, rotated, "eng")
                if again[1] > best[1]:
                    best, img = again, rotated
        if "hin" in await run_ocr_work(_tesseract_languages, pytesseract):
            try:
                hindi = await run_ocr_work(_read, img, "hin+eng")
            except Exception as exc:
                _log.debug("Tesseract hin+eng unavailable: %s", exc)
            else:
                if _devanagari_share(hindi[0]) >= DEVANAGARI_MIN_SHARE:
                    best = hindi
        return best

    async def _llm_vision_ocr(
        self,
        img: Any,
        *,
        provider: Any = None,
        tenant_id: str | None = None,
    ) -> tuple[str, float, str]:
        """LLM-vision OCR of one page, holding one of the process-wide vision
        slots (OCR_VISION_CONCURRENCY): pages fall back concurrently, bounded."""
        async with ocr_vision_slot(tenant_id):
            return await self._llm_vision_ocr_unbounded(
                img, provider=provider, tenant_id=tenant_id
            )

    async def _llm_vision_ocr_unbounded(
        self,
        img: Any,
        *,
        provider: Any = None,
        tenant_id: str | None = None,
    ) -> tuple[str, float, str]:
        """Use LLM vision to extract text from an image.

        Charged to ``tenant_id`` (else the request / goal scope). A budget
        refusal is raised — never turned into an empty page of text.
        """
        from app.providers.guarded_completion import DecisionBudgetExceededError

        if provider is None:
            provider = getattr(self, "_fallback_provider", None)
        if provider is None:
            # Agent-callable OCR (extract_document) often runs with no injected
            # provider. Rather than return empty, resolve the system-configured
            # provider so OCR works standalone; the vision model is still chosen
            # per-request via _ocr_model() below.
            try:
                from app.providers.registry import resolve_provider

                provider = resolve_provider()
                # Resolved once per engine, not once per page.
                self._fallback_provider = provider
            except Exception as exc:
                _log.warning("No provider for LLM vision OCR (%s); empty text", exc)
                _note_page_failure("no LLM provider is available for vision OCR")
                return "", 0.0, "llm_vision"

        img_b64 = await run_ocr_work(self._image_to_base64, img)  # PNG encode: off the loop
        try:
            from app.ai_router.resolve import dispatch_provider
            from app.providers.base import CompletionRequest, Message

            # Each OCR / vision model (the head and every fallback) runs at its
            # own Model Registry endpoint, whatever provider was injected.
            provider = dispatch_provider(provider)

            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Extract all text from this image. "
                            "Return only the extracted text, no commentary."
                        ),
                        image_data=img_b64,
                    )
                ],
                # OCR uses the system-configured model (OCR_MODEL/NVIDIA_MODEL/…);
                # the empty case lets the provider fall back to its default model.
                model=_ocr_model(),
            )
            from app.providers.guarded_completion import (
                complete_decision,
                vision_timeout_seconds,
            )

            fallbacks = _ocr_fallback_models(req.model)
            response = await complete_decision(
                provider,
                req,
                role="ocr_vision",
                tenant_id=tenant_id,
                timeout_seconds=vision_timeout_seconds(),
                # Another configured vision model takes over when this one is down,
                # times out or answers empty (it used to be a single-model call).
                fallback_models=fallbacks,
                # Up to OCR_VISION_CONCURRENCY pages fall back at once: each one
                # reserves its worst-case cost BEFORE the call, so together they
                # can never spend past the budget (settled to the real cost after).
                reserve_usd=vision_reserve_usd(
                    img, [req.model, *fallbacks], max_output_tokens=req.max_tokens
                ),
            )
            return response.content, VISION_ASSUMED_CONFIDENCE, "llm_vision"
        except DecisionBudgetExceededError:
            raise
        except Exception as exc:
            _log.warning("LLM vision OCR failed: %s", exc)
            # The exception type only: provider messages may echo request details.
            _note_page_failure(f"LLM vision OCR failed ({type(exc).__name__})")
            return "", 0.0, "llm_vision"

    @staticmethod
    def _image_to_base64(img: Any) -> str:
        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
        except Exception:
            return ""
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _preprocess_image(img: Any) -> Any:
        """Apply preprocessing to improve OCR accuracy.

        Converts to grayscale and applies adaptive thresholding (binarization).
        Requires Pillow >= 10.0.0 (already a dep).
        """
        try:
            from PIL import ImageFilter, ImageOps  # type: ignore[import-untyped]

            # Convert to grayscale
            if img.mode != "L":
                img = img.convert("L")
            # Apply slight sharpening to improve character edges
            img = img.filter(ImageFilter.SHARPEN)
            # Auto-contrast to improve binarization. Clip only the bright end: on a
            # sparse page the ink is under 2 % of the pixels, and clipping 2 % at the
            # dark end landed on the paper's grey and turned the page black.
            img = ImageOps.autocontrast(img, cutoff=(0, 2))
            return img
        except Exception:
            return img  # graceful fallback: return original if preprocessing fails
