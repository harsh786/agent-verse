"""Deterministic scanned documents for the OCR-* scenarios (real OCR engines only).

Reuses the suite's scan renderer (``corpus.render_scan``) and the KB-UPLOAD-HARD scans
(``corpus_hard``: 3-page inspection report, sideways gate pass, low-quality receipt)
and adds: a two-column scan, a ruled table, a Hindi (Devanagari) scan, a JPG UI
screenshot, a handwritten-looking note, a readable + noise two-page PDF and the
refusal inputs (truncated PNG, a PDF that cannot be rasterised, an over-cap upload).

Every document carries facts no other document has, so concurrent OCR results can be
checked for cross-talk. Pillow + fpdf2 only (already dependencies); fonts come from
the host — the Devanagari case reports itself unavailable (``None``) without a font.
"""

from __future__ import annotations

import io
import os
import random
from dataclasses import dataclass, field
from typing import Any

from tests.real_world import corpus_hard as ch
from tests.real_world.corpus import _font, render_scan

SEED = 20261008
PDF = "application/pdf"
PNG = "image/png"
JPG = "image/jpeg"
DEVANAGARI_FONTS = (
    os.getenv("RW_DEVANAGARI_FONT", ""),
    "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
    "/System/Library/Fonts/Supplemental/DevanagariMT.ttc",
    "/System/Library/Fonts/Kohinoor.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
)


@dataclass
class OcrDoc:
    case: str
    filename: str
    mime: str
    data: bytes
    facts: list[str]  # each must be read (normalised substring match)
    pages: int = 1
    question: str = ""  # a known-answer question for the KB leg
    answer_any: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def fact_tokens(self) -> list[str]:
        return [f.lower() for f in self.facts]


def _png(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf(images: list[Any]) -> bytes:
    """An image-only PDF (a scan) with fixed metadata, so the bytes are deterministic."""
    import datetime as dt

    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_creation_date(dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
    for img in images:
        pdf.add_page()
        buf = io.BytesIO()
        img.convert("L").save(buf, format="PNG")
        buf.seek(0)
        pdf.image(buf, x=0, y=0, w=210, h=297)
    return bytes(pdf.output())


def two_column() -> OcrDoc:
    from PIL import Image, ImageDraw

    rng = random.Random(f"{SEED}:2col")
    img = Image.new("L", (1654, 2339), 246)
    draw = ImageDraw.Draw(img)
    font = _font(40)
    left = ["HARBOUR NOTICE 41/2026", "Berth B-14 dredging depth", "now 13.2 m at chart datum.",
            "Night berthing allowed", "for vessels under 240 m."]
    right = ["PILOTAGE", "Pilot boarding moved to", "buoy PB-7 from 1 November.",
             "VHF channel 12 for", "all pilot requests."]
    for i, line in enumerate(left):
        draw.text((120, 200 + i * 90), line, fill=20, font=font)
    for i, line in enumerate(right):
        draw.text((900, 200 + i * 90), line, fill=20, font=font)
    draw.line((850, 180, 850, 700), fill=90, width=3)
    for _ in range(1654 * 2339 // 1000):
        draw.point((rng.randrange(1654), rng.randrange(2339)), fill=rng.randint(170, 215))
    return OcrDoc("two-column", "harbour-notice-41-2026-scan.pdf", PDF,
                  _pdf([img.rotate(0.4, fillcolor=246)]), ["13.2 m", "PB-7"],
                  question="What is the dredging depth at berth B-14?",
                  answer_any=["13.2"])


def table_scan() -> OcrDoc:
    from PIL import Image, ImageDraw

    img = Image.new("L", (1654, 1200), 248)
    draw = ImageDraw.Draw(img)
    font = _font(38)
    rows = [["Gate", "Equipment", "Count"], ["Gate 2", "forklifts", "11"],
            ["Gate 4", "reefer plugs", "36"], ["Gate 6", "weighbridges", "2"]]
    x0, y0, cw, rh = 120, 160, [300, 520, 220], 110
    for r, row in enumerate(rows):
        x = x0
        for c, cell in enumerate(row):
            draw.rectangle((x, y0 + r * rh, x + cw[c], y0 + (r + 1) * rh), outline=40, width=3)
            draw.text((x + 20, y0 + r * rh + 30), cell, fill=15, font=font)
            x += cw[c]
    return OcrDoc("table", "gate-equipment-register-scan.png", PNG,
                  _png(img.rotate(-0.3, fillcolor=248)), ["reefer plugs", "36"],
                  question="How many reefer plugs are at Gate 4?", answer_any=["36"])


def devanagari_font(size: int) -> Any | None:
    from PIL import ImageFont

    for path in DEVANAGARI_FONTS:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def hindi_scan() -> OcrDoc | None:
    """A Hindi stock note (Devanagari); None when the host has no Devanagari font."""
    from PIL import Image, ImageDraw

    font = devanagari_font(64)
    if font is None:
        return None
    img = Image.new("L", (1654, 900), 246)
    draw = ImageDraw.Draw(img)
    lines = ["गोदाम नंबर 4721", "चावल : 950 बोरा", "माल ठीक है"]
    for i, line in enumerate(lines):
        draw.text((140, 160 + i * 150), line, fill=15, font=font)
    return OcrDoc("hindi", "godam-4721-stock-note-scan.png", PNG, _png(img),
                  ["4721", "950"], question="गोदाम नंबर 4721 में चावल के कितने बोरे हैं?",
                  answer_any=["950"],
                  notes="Devanagari rendered without libraqm shaping when Pillow lacks it")


def screenshot_jpg() -> OcrDoc:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1280, 720), (245, 247, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1280, 80), fill=(33, 52, 99))
    draw.text((30, 20), "Dispatch Console", fill=(255, 255, 255), font=_font(36))
    draw.rectangle((40, 140, 1240, 360), outline=(180, 186, 199), width=2, fill=(255, 255, 255))
    draw.text((70, 170), "Order ORD-55120", fill=(20, 20, 20), font=_font(40))
    draw.text((70, 240), "Status: OUT FOR DELIVERY", fill=(12, 120, 60), font=_font(40))
    draw.text((70, 300), "Rider: Imran Shaikh  ETA 14:20", fill=(60, 60, 60), font=_font(32))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return OcrDoc("screenshot-jpg", "dispatch-console-ord-55120.jpg", JPG, buf.getvalue(),
                  ["ORD-55120", "OUT FOR DELIVERY"],
                  question="What is the status of order ORD-55120?",
                  answer_any=["out for delivery"])


def handwritten_note() -> OcrDoc:
    """Per-letter jitter, a wobbling baseline and stray pen strokes (handwriting-like)."""
    from PIL import Image, ImageDraw

    rng = random.Random(f"{SEED}:hand")
    img = Image.new("L", (1600, 700), 250)
    draw = ImageDraw.Draw(img)
    lines = ["Call Arvind about", "crate 88 by Friday"]
    for li, line in enumerate(lines):
        x, base = 120, 180 + li * 220
        for ch_ in line:
            font = _font(rng.randint(66, 76))
            y = base + rng.randint(-7, 7)
            draw.text((x, y), ch_, fill=rng.randint(10, 60), font=font)
            x += int(font.getlength(ch_)) + rng.randint(0, 5)
    for _ in range(14):
        x1, y1 = rng.randrange(1600), rng.randrange(700)
        draw.line((x1, y1, x1 + rng.randint(-60, 60), y1 + rng.randint(-20, 20)),
                  fill=rng.randint(120, 190), width=2)
    return OcrDoc("handwritten", "warehouse-sticky-note-crate-88.png", PNG,
                  _png(img.rotate(-1.5, fillcolor=250)), ["crate 88"],
                  question="Which crate must Arvind be called about?", answer_any=["88"])


def readable_plus_noise() -> OcrDoc:
    """Page 1 readable; page 2 pure speckle noise (nothing to read)."""
    from PIL import Image

    rng = random.Random(f"{SEED}:noise")
    page1 = render_scan(["DAMAGE REPORT DR-3091", "Container MSKU 774120 dented",
                         "Surveyor: Hana Kobayashi"], seed=71)
    noise = Image.new("L", (1654, 2339), 246)
    px = noise.load()
    for _ in range(1654 * 2339 // 6):
        px[rng.randrange(1654), rng.randrange(2339)] = rng.randint(0, 255)
    return OcrDoc("readable-plus-noise", "damage-report-dr-3091-with-noise-page.pdf", PDF,
                  _pdf([page1, noise]), ["DR-3091"], pages=2)


def multipage() -> OcrDoc:
    """KB-UPLOAD-HARD's 3-page crane inspection scan (same pages, deterministic bytes)."""
    images = [render_scan(lines, seed=31 + i, skew=(-0.5, 0.7, -0.3)[i])
              for i, lines in enumerate(ch.SCAN_PAGES)]
    return OcrDoc("multipage-3p", "crane-inspection-report-cir-2026-0912-scan.pdf", PDF,
                  _pdf(images),
                  ["CIR-2026-0912", "2.4 mm", "Ferreira"], pages=3,
                  question="What was the hoist brake wear measured on crane STS-09?",
                  answer_any=["2.4 mm", "2.4mm"])


def rotated() -> OcrDoc:
    d = ch.build_png_rotated()
    return OcrDoc("rotated", d.filename, d.mime, d.data, ["GP-77120", "Wieczorek"],
                  question="Who is the driver on gate pass GP-77120?",
                  answer_any=["tomasz wieczorek", "wieczorek"])


def low_quality() -> OcrDoc:
    d = ch.build_png_lowq()
    return OcrDoc("low-quality", d.filename, d.mime, d.data, ["TJ-5531"],
                  question="Which towage job number is on the Heron Tugs receipt?",
                  answer_any=["tj-5531"])


def readable_docs() -> list[OcrDoc]:
    docs = [two_column(), table_scan(), screenshot_jpg(), handwritten_note(), multipage(),
            rotated(), low_quality()]
    hindi = hindi_scan()
    if hindi is not None:
        docs.append(hindi)
    return docs


# ── Refusal inputs ───────────────────────────────────────────────────────────


def truncated_png() -> bytes:
    """A real PNG cut in half: header valid, image data missing."""
    data = table_scan().data
    return data[: len(data) // 3]


def unrasterisable_pdf() -> bytes:
    """``%PDF-`` header followed by bytes no renderer can draw."""
    rng = random.Random(f"{SEED}:badpdf")
    return b"%PDF-1.7\n" + bytes(rng.randrange(256) for _ in range(4096)) + b"\n%%EOF\n"


def oversize_png(limit_bytes: int) -> bytes:
    """A PNG-signed body one byte above ``limit_bytes`` (refused before decoding)."""
    head = b"\x89PNG\r\n\x1a\n"
    block = bytes(range(256)) * 64
    body = (block * (limit_bytes // len(block) + 2))[: limit_bytes + 1 - len(head)]
    return head + body


def contains_fact(text: str, fact: str) -> bool:
    """OCR-tolerant containment: case / whitespace insensitive."""
    norm = " ".join(text.lower().split())
    return " ".join(fact.lower().split()) in norm


def cross_talk(results: dict[str, str], docs: dict[str, OcrDoc]) -> list[str]:
    """Facts of one document found in another document's OCR result."""
    out = []
    for case, text in results.items():
        for other, doc in docs.items():
            if other == case:
                continue
            for fact in doc.facts:
                if len(fact) >= 6 and contains_fact(text, fact):
                    out.append(f"{case} contains {other}'s fact {fact!r}")
    return out
