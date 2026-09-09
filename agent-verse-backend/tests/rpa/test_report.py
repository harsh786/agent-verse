"""WS-5: RPA scrape → structured report → rendered PDF artifact.

Proves the deliverable path: a sequence of ``RPAResult`` objects from a scrape
run is assembled into a provenance-tagged ``ScrapeReport``, rendered to a real
PDF (fpdf2), and persisted through the artifact store as a non-empty PDF whose
content includes the scraped text.
"""

from __future__ import annotations

import base64

import pytest

from app.rpa.executor import RPAResult
from app.rpa.report import (
    ScrapeReport,
    assemble_report_from_results,
    build_and_store_report_pdf,
    render_report_pdf,
    run_scrape_report,
)

# A 1x1 PNG so screenshot embedding is exercised with real image bytes.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _scrape_results() -> list[RPAResult]:
    return [
        RPAResult(success=True, output="Q3 revenue was 1.2M", artifact_name="Extracted: revenue"),
        RPAResult(
            success=True,
            output="",
            artifact_url=f"data:image/png;base64,{base64.b64encode(_PNG_1x1).decode()}",
            artifact_name="landing-screenshot",
        ),
        RPAResult(success=False, output="nav failed", error="timeout"),  # dropped
    ]


def test_assemble_report_splits_text_and_screenshots() -> None:
    report = assemble_report_from_results(
        _scrape_results(),
        source_url="https://example.com/q3",
        provenance={"selector": "table.financials"},
    )
    assert isinstance(report, ScrapeReport)
    assert report.source_url == "https://example.com/q3"
    # The failed result is dropped; one text section + one screenshot survive.
    assert len(report.sections) == 1
    assert "1.2M" in report.sections[0].body
    assert len(report.screenshots) == 1
    assert report.screenshots[0].png_bytes == _PNG_1x1
    # Provenance is preserved (source_url + the custom selector).
    assert report.provenance["source_url"] == "https://example.com/q3"
    assert report.provenance["selector"] == "table.financials"
    assert not report.is_empty


def test_render_report_pdf_is_valid_and_contains_scraped_text() -> None:
    report = assemble_report_from_results(
        _scrape_results(), source_url="https://example.com/q3"
    )
    pdf = render_report_pdf(report, compress=False)
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500  # non-trivial
    # The scraped content and provenance are actually in the document.
    assert b"1.2M" in pdf
    assert b"example.com/q3" in pdf


def test_render_report_pdf_embeds_screenshot() -> None:
    report = ScrapeReport(title="t", source_url="u")
    from app.rpa.report import ReportScreenshot

    report.screenshots.append(ReportScreenshot(caption="shot", png_bytes=_PNG_1x1))
    pdf = render_report_pdf(report)
    assert pdf[:4] == b"%PDF"
    # An embedded image => the PDF carries an XObject image resource.
    assert b"/Image" in pdf or b"/XObject" in pdf


@pytest.mark.asyncio
async def test_build_and_store_report_pdf_persists_nonempty_pdf(tmp_path) -> None:
    from app.rpa.artifacts import RPAArtifactStore

    store = RPAArtifactStore(base_dir=tmp_path)
    report = assemble_report_from_results(
        _scrape_results(), source_url="https://example.com/q3"
    )
    artifact = await build_and_store_report_pdf(
        report, artifact_store=store, goal_id="goal-1", name="q3-report.pdf"
    )
    assert artifact.size_bytes > 500
    assert artifact.name.endswith(".pdf")
    # The stored bytes are a real PDF containing the scraped text.
    stored = (tmp_path / "goal-1" / "q3-report.pdf").read_bytes()
    assert stored[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_run_scrape_report_orchestrates_executor_sequence() -> None:
    """run_scrape_report drives open_url → extract_text → screenshot into a report."""

    class _FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(
            self, *, tool_name, arguments, session_id, tenant_id, goal_id, allow_http_fetch=False
        ):
            self.calls.append(tool_name)
            if tool_name == "rpa_extract_text":
                return RPAResult(success=True, output="Scraped body: revenue 1.2M", artifact_name="extract")
            if tool_name == "rpa_screenshot":
                return RPAResult(
                    success=True,
                    artifact_url=f"data:image/png;base64,{base64.b64encode(_PNG_1x1).decode()}",
                    artifact_name="page",
                )
            return RPAResult(success=True, output="navigated")

    ex = _FakeExecutor()
    report, results = await run_scrape_report(
        ex, url="https://example.com", selectors=["main"], goal_id="g1"
    )
    assert ex.calls == ["rpa_open_url", "rpa_extract_text", "rpa_screenshot"]
    assert len(results) == 3
    # A shared session id is threaded through and recorded in provenance.
    assert "session_id" in report.provenance
    assert any("1.2M" in s.body for s in report.sections)
    assert len(report.screenshots) == 1


@pytest.mark.asyncio
async def test_build_and_store_supports_async_store() -> None:
    """The store helper awaits an async write_bytes (MinIO/S3 backend)."""
    from app.rpa.artifacts import RPAArtifact

    class _AsyncStore:
        def __init__(self) -> None:
            self.written: bytes = b""

        async def write_bytes(self, *, goal_id: str, name: str, content: bytes) -> RPAArtifact:
            self.written = content
            return RPAArtifact(name=name, size_bytes=len(content))

    store = _AsyncStore()
    report = ScrapeReport(title="t", source_url="u")
    report.sections.append(  # ensure non-empty
        __import__("app.rpa.report", fromlist=["ReportSection"]).ReportSection(
            heading="h", body="body text"
        )
    )
    artifact = await build_and_store_report_pdf(
        report, artifact_store=store, goal_id="g", name="r.pdf"
    )
    assert artifact.size_bytes > 0
    assert store.written[:4] == b"%PDF"
