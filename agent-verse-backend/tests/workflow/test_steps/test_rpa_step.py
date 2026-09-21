"""Tests for RPAStepNode — browser automation (scrape) + report generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus
from app.workflow.steps.rpa_step import RPAStepNode


def _ctx() -> ContextResolver:
    return ContextResolver()


def _state(**kwargs) -> dict:
    defaults = {
        "run_id": "run-1", "workflow_id": "wf-1", "tenant_id": "t-1",
        "inputs": {}, "step_outputs": {}, "vars": {},
        "status": WorkflowRunStatus.RUNNING,
    }
    defaults.update(kwargs)
    return defaults


def _report(sections: bool = True) -> SimpleNamespace:
    from app.rpa.report import ReportSection, ScrapeReport

    report = ScrapeReport(title="My Report", source_url="https://example.com")
    if sections:
        report.sections.append(ReportSection(heading="Intro", body="hello"))
    return report


@pytest.mark.asyncio
async def test_rpa_step_missing_url_returns_degraded_no_exception() -> None:
    step = StepDefinition(id="r1", type="rpa", input={})
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())
    result = await node.execute(_state())  # type: ignore[arg-type]
    out = result["step_outputs"]["r1"]
    assert out["degraded"] is True
    assert "url" in out["error"]


@pytest.mark.asyncio
async def test_rpa_step_test_run_uses_mock_override() -> None:
    step = StepDefinition(id="r1", type="rpa", input={"url": "https://example.com"})
    node = RPAStepNode(step, _ctx())
    state = _state(is_test_run=True, mock_overrides={"r1": {"report": "mocked"}})
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["r1"] == {"report": "mocked"}
    assert "r1" in result["step_timings"]


@pytest.mark.asyncio
async def test_rpa_step_success_generates_pdf() -> None:
    report = _report()
    step = StepDefinition(id="r1", type="rpa", input={"url": "https://example.com"})
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())

    with (
        patch("app.rpa.report.run_scrape_report", AsyncMock(return_value=(report, []))),
        patch(
            "app.rpa.report.build_and_store_report_pdf",
            AsyncMock(return_value=SimpleNamespace(uri="s3://bucket/report.pdf")),
        ),
    ):
        result = await node.execute(_state())  # type: ignore[arg-type]

    out = result["step_outputs"]["r1"]
    assert out["report"]["title"] == "My Report"
    assert out["section_count"] == 1
    assert out["degraded"] is False
    assert out["report_pdf_uri"] == "s3://bucket/report.pdf"


@pytest.mark.asyncio
async def test_rpa_step_generate_pdf_false_skips_pdf() -> None:
    report = _report()
    step = StepDefinition(
        id="r1", type="rpa", input={"url": "https://example.com", "generate_pdf": False}
    )
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())

    build_pdf = AsyncMock()
    with (
        patch("app.rpa.report.run_scrape_report", AsyncMock(return_value=(report, []))),
        patch("app.rpa.report.build_and_store_report_pdf", build_pdf),
    ):
        result = await node.execute(_state())  # type: ignore[arg-type]

    out = result["step_outputs"]["r1"]
    assert "report_pdf_uri" not in out
    assert "report_pdf_error" not in out
    build_pdf.assert_not_called()


@pytest.mark.asyncio
async def test_rpa_step_pdf_failure_sets_error_but_keeps_report() -> None:
    report = _report()
    step = StepDefinition(id="r1", type="rpa", input={"url": "https://example.com"})
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())

    with (
        patch("app.rpa.report.run_scrape_report", AsyncMock(return_value=(report, []))),
        patch(
            "app.rpa.report.build_and_store_report_pdf",
            AsyncMock(side_effect=RuntimeError("pdf render failed")),
        ),
    ):
        result = await node.execute(_state())  # type: ignore[arg-type]

    out = result["step_outputs"]["r1"]
    assert out["report"]["title"] == "My Report"
    assert "report_pdf_uri" not in out
    assert "pdf render failed" in out["report_pdf_error"]


@pytest.mark.asyncio
async def test_rpa_step_scrape_exception_returns_degraded() -> None:
    step = StepDefinition(id="r1", type="rpa", input={"url": "https://example.com"})
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())

    with patch(
        "app.rpa.report.run_scrape_report",
        AsyncMock(side_effect=RuntimeError("navigation timed out")),
    ):
        result = await node.execute(_state())  # type: ignore[arg-type]

    out = result["step_outputs"]["r1"]
    assert out["degraded"] is True
    assert "navigation timed out" in out["error"]


@pytest.mark.asyncio
async def test_rpa_step_degraded_true_when_report_empty() -> None:
    report = _report(sections=False)
    step = StepDefinition(
        id="r1", type="rpa", input={"url": "https://example.com", "generate_pdf": False}
    )
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())

    with patch("app.rpa.report.run_scrape_report", AsyncMock(return_value=(report, []))):
        result = await node.execute(_state())  # type: ignore[arg-type]

    assert result["step_outputs"]["r1"]["degraded"] is True


@pytest.mark.asyncio
async def test_rpa_step_string_selector_converted_to_list() -> None:
    report = _report()
    step = StepDefinition(
        id="r1",
        type="rpa",
        input={"url": "https://example.com", "selectors": "h1", "generate_pdf": False},
    )
    node = RPAStepNode(step, _ctx(), rpa_executor=AsyncMock(), rpa_artifact_store=Mock())

    mock_run = AsyncMock(return_value=(report, []))
    with patch("app.rpa.report.run_scrape_report", mock_run):
        await node.execute(_state())  # type: ignore[arg-type]

    assert mock_run.call_args.kwargs["selectors"] == ["h1"]
