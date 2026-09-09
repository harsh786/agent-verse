"""WS-5: RPA scrape → structured report → rendered PDF artifact.

An RPA scrape run produces a sequence of :class:`~app.rpa.executor.RPAResult`
objects (extracted text, screenshots). This module assembles those into a
provenance-tagged :class:`ScrapeReport` and renders it to a real PDF via
``fpdf2`` (pure-python, always available), which is then persisted through the
RPA artifact store as a downloadable deliverable.

One reachable path: assemble → render → store. The renderer degrades gracefully
(latin-1 sanitisation, per-screenshot embed guard) so a scrape never fails to
produce a document.
"""

from __future__ import annotations

import base64
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any

from app.rpa.executor import RPAResult


@dataclass
class ReportSection:
    """One block of scraped textual content in the report."""

    heading: str
    body: str


@dataclass
class ReportScreenshot:
    """A screenshot captured during the scrape (raw PNG bytes + caption)."""

    caption: str
    png_bytes: bytes


@dataclass
class ScrapeReport:
    """Structured, provenance-tagged result of an RPA scrape run."""

    title: str
    source_url: str
    scraped_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sections: list[ReportSection] = field(default_factory=list)
    screenshots: list[ReportScreenshot] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.sections and not self.screenshots


def assemble_report_from_results(
    results: list[RPAResult],
    *,
    source_url: str,
    title: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> ScrapeReport:
    """Turn a sequence of ``RPAResult`` from a scrape run into a structured report.

    * successful text-bearing results become :class:`ReportSection`s
    * successful screenshot results (``data:image/png;base64`` ``artifact_url``)
      become :class:`ReportScreenshot`s (bytes decoded once, here)
    * failed results are dropped (their error is not scraped content)
    """
    report = ScrapeReport(
        title=title or f"RPA Scrape Report — {source_url}",
        source_url=source_url,
        provenance={"source_url": source_url, **(provenance or {})},
    )
    for i, r in enumerate(results):
        if not r.success:
            continue
        if r.artifact_url and r.artifact_url.startswith("data:image"):
            png = _decode_data_uri_png(r.artifact_url)
            if png is not None:
                report.screenshots.append(
                    ReportScreenshot(
                        caption=r.artifact_name or f"screenshot-{i + 1}", png_bytes=png
                    )
                )
                continue
        if r.output and r.output.strip():
            report.sections.append(
                ReportSection(heading=r.artifact_name or f"Step {i + 1}", body=r.output.strip())
            )
    return report


def _decode_data_uri_png(uri: str) -> bytes | None:
    try:
        return base64.b64decode(uri.split(",", 1)[1])
    except Exception:
        return None


def render_report_pdf(report: ScrapeReport, *, compress: bool = True) -> bytes:
    """Render a :class:`ScrapeReport` to a real PDF and return its bytes.

    ``compress=False`` yields an uncompressed PDF (text is literal in the byte
    stream) — used by tests to assert content is present without a PDF reader.
    """
    from fpdf import FPDF  # lazy: keep the base import graph light
    from fpdf.enums import XPos, YPos

    # Every multi_cell resets x to the left margin and advances y, so the next
    # full-width (w=0) cell always has the full page width available.
    def _cell(height: float, text: str) -> None:
        pdf.multi_cell(0, height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf = FPDF()
    pdf.set_compression(compress)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    _cell(10, _latin1(report.title))

    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(90, 90, 90)
    _cell(5, _latin1(f"Source: {report.source_url}"))
    _cell(5, _latin1(f"Scraped at: {report.scraped_at}"))
    extra_prov = ", ".join(
        f"{k}={v}" for k, v in report.provenance.items() if k != "source_url"
    )
    if extra_prov:
        _cell(5, _latin1(f"Provenance: {extra_prov}"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    if report.is_empty:
        pdf.set_font("Helvetica", "I", 10)
        _cell(6, "No content was extracted from this scrape.")

    for section in report.sections:
        pdf.set_font("Helvetica", "B", 12)
        _cell(7, _latin1(section.heading))
        pdf.set_font("Helvetica", size=10)
        _cell(6, _latin1(section.body))
        pdf.ln(2)

    for shot in report.screenshots:
        pdf.set_font("Helvetica", "I", 9)
        _cell(5, _latin1(shot.caption))
        try:
            pdf.image(io.BytesIO(shot.png_bytes), w=pdf.epw)
        except Exception:
            pdf.set_font("Helvetica", size=9)
            _cell(5, "[screenshot could not be embedded]")
        pdf.ln(2)

    return bytes(pdf.output())


def _latin1(text: str) -> str:
    """fpdf2 core fonts are latin-1; replace unencodable chars so rendering never crashes."""
    return text.encode("latin-1", "replace").decode("latin-1")


async def run_scrape_report(
    executor: Any,
    *,
    url: str,
    tenant_id: str = "",
    goal_id: str = "",
    selectors: list[str] | None = None,
    session_id: str | None = None,
    title: str | None = None,
    allow_http_fetch: bool = False,
) -> tuple[ScrapeReport, list[RPAResult]]:
    """Run a scrape sequence via an ``RPAExecutor`` and assemble a report.

    Sequence (all sharing one session so page state persists): open the URL →
    extract text (per selector, or the whole page) → screenshot. Works with the
    real Playwright executor and with the simulation fallback (no browser), so
    it always yields a report. ``allow_http_fetch`` opts into the WS-13 real-HTTP
    fallback (browser-less REAL page text) for the KB scrape path. Returns
    ``(report, raw_results)``.
    """
    sid = session_id or uuid.uuid4().hex
    results: list[RPAResult] = []

    results.append(
        await executor.execute(
            tool_name="rpa_open_url",
            arguments={"url": url},
            session_id=sid,
            tenant_id=tenant_id,
            goal_id=goal_id,
            allow_http_fetch=allow_http_fetch,
        )
    )
    for sel in selectors or [None]:  # type: ignore[list-item]
        args: dict[str, Any] = {"selector": sel} if sel else {}
        results.append(
            await executor.execute(
                tool_name="rpa_extract_text",
                arguments=args,
                session_id=sid,
                tenant_id=tenant_id,
                goal_id=goal_id,
                allow_http_fetch=allow_http_fetch,
            )
        )
    results.append(
        await executor.execute(
            tool_name="rpa_screenshot",
            arguments={"name": "page"},
            session_id=sid,
            tenant_id=tenant_id,
            goal_id=goal_id,
            allow_http_fetch=allow_http_fetch,
        )
    )

    report = assemble_report_from_results(
        results,
        source_url=url,
        title=title,
        provenance={"session_id": sid, "steps": len(results)},
    )
    return report, results


async def build_and_store_report_pdf(
    report: ScrapeReport,
    *,
    artifact_store: Any,
    goal_id: str,
    name: str = "rpa-report.pdf",
) -> Any:
    """Render the report to PDF and persist it via the artifact store.

    Works with both the sync ``RPAArtifactStore`` and the async
    ``MinIOArtifactStore`` (their ``write_bytes`` share a signature; the async
    one returns an awaitable). Returns the store's artifact reference.
    """
    pdf_bytes = render_report_pdf(report)
    result = artifact_store.write_bytes(goal_id=goal_id, name=name, content=pdf_bytes)
    if isawaitable(result):
        result = await result
    return result
