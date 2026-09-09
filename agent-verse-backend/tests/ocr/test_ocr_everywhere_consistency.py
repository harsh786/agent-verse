"""WS-14: OCR is ONE engine reachable from multiple execution routes.

Proves the "same extracted text, one engine, no duplication" invariant across
the routes that are wired: the agent-callable tool (goal/agent execution) and
the workflow ``ocr`` step. Both funnel the same bytes into the same
``OcrEngine.extract_any`` and surface the same text — so no route re-implements
OCR. Uses a shared stub engine for determinism (no Tesseract needed).

Remaining WS-14 (documented, not yet done): exposing OCR as a *builtin agent
tool* in the MCP catalog/handler wiring so a goal/org-mission agent can call it
by name end-to-end; and the standalone `/ocr/extract`→KB path (owned by WS-13).
"""

from __future__ import annotations

import base64

import pytest

from app.ocr.models import DocumentType, OcrResult
from app.tools.ocr_tool import OcrDocumentTool
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.steps.ocr_step import OcrStepNode

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_EXPECTED_TEXT = "ACME INVOICE total 1,250.00"


class _StubEngine:
    """Records every extract_any call and returns a fixed OcrResult."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def extract_any(self, data, *, content_type=None, filename=None, provider=None):
        self.calls.append(data)
        return OcrResult(
            raw_text=_EXPECTED_TEXT,
            document_type=DocumentType.INVOICE,
            overall_confidence=0.91,
            page_count=1,
            source_format="image",
        )


def _make_state():
    return {
        "run_id": "r1", "tenant_id": "t1", "inputs": {}, "step_outputs": {}, "vars": {},
        "cost_usd": 0.0, "tokens_used": 0, "step_timings": {},
        "is_test_run": False, "mock_overrides": {},
    }


@pytest.mark.asyncio
async def test_tool_and_workflow_step_yield_same_text_via_one_engine() -> None:
    engine = _StubEngine()
    doc_b64 = base64.b64encode(_PNG).decode()

    # Route 1 — agent/goal execution via the OcrDocumentTool.
    tool_out = await OcrDocumentTool(ocr_engine=engine).execute(
        document_base64=doc_b64, filename="invoice.png"
    )

    # Route 2 — a workflow 'ocr' step.
    step = StepDefinition(id="o1", type="ocr", input={"document_base64": doc_b64})
    step_result = await OcrStepNode(step, ContextResolver(), ocr_engine=engine).execute(_make_state())
    step_out = step_result["step_outputs"]["o1"]

    # Same extracted text from both routes.
    assert tool_out["raw_text"] == _EXPECTED_TEXT
    assert step_out["raw_text"] == _EXPECTED_TEXT
    assert tool_out["raw_text"] == step_out["raw_text"]

    # ONE engine: both routes delegated to the same extract_any with the same bytes.
    assert len(engine.calls) == 2
    assert engine.calls[0] == _PNG == engine.calls[1]
