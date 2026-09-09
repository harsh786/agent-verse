"""Tests for step nodes using FakeProvider / mock services."""
from __future__ import annotations

import pytest
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition


def make_state(**kwargs):
    base = {
        "run_id": "r1", "tenant_id": "t1",
        "inputs": {}, "step_outputs": {}, "vars": {},
        "cost_usd": 0.0, "tokens_used": 0, "step_timings": {},
        "is_test_run": False, "mock_overrides": {},
        "foreach_progress": {}, "labels": {}, "run_metadata": {},
        "vault_refs_used": set(),
    }
    base.update(kwargs)
    return base


# ── ToolStepNode ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_step_no_client():
    """Without MCPClient, returns mock output."""
    from app.workflow.steps.tool_step import ToolStepNode
    step = StepDefinition(id="s1", type="tool", tool="test.tool", input={"x": "1"})
    ctx = ContextResolver()
    node = ToolStepNode(step, ctx)
    state = make_state()
    result = await node.execute(state)
    assert "s1" in result["step_outputs"]
    assert result["step_outputs"]["s1"].get("_mock") is True


@pytest.mark.asyncio
async def test_tool_step_mock_override():
    from app.workflow.steps.tool_step import ToolStepNode
    step = StepDefinition(id="s1", type="tool", tool="t")
    ctx = ContextResolver()
    node = ToolStepNode(step, ctx)
    state = make_state(is_test_run=True, mock_overrides={"s1": {"mocked": 42}})
    result = await node.execute(state)
    assert result["step_outputs"]["s1"] == {"mocked": 42}


# ── LLMStepNode ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_step_no_provider():
    from app.workflow.steps.llm_step import LLMStepNode
    step = StepDefinition(id="llm1", type="llm", prompt="Classify: {{inputs.text}}")
    ctx = ContextResolver()
    node = LLMStepNode(step, ctx)
    state = make_state(inputs={"text": "hello"})
    result = await node.execute(state)
    out = result["step_outputs"]["llm1"]
    assert "result" in out or "FakeProvider" in str(out)


# ── TransformStepNode ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transform_step():
    from app.workflow.steps.transform_step import TransformStepNode
    step = StepDefinition(id="t1", type="transform", input={"name": "{{inputs.raw_name}}", "static": "hello"})
    ctx = ContextResolver()
    node = TransformStepNode(step, ctx)
    state = make_state(inputs={"raw_name": "Alice"})
    result = await node.execute(state)
    assert result["step_outputs"]["t1"]["name"] == "Alice"
    assert result["step_outputs"]["t1"]["static"] == "hello"


# ── SetVariableStepNode ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_variable_step():
    from app.workflow.steps.set_variable_step import SetVariableStepNode
    step = StepDefinition(id="sv1", type="set_variable", var_name="counter", var_value="{{inputs.start}}", value_type="number")
    ctx = ContextResolver()
    node = SetVariableStepNode(step, ctx)
    state = make_state(inputs={"start": "5"})
    result = await node.execute(state)
    assert result["vars"]["counter"] == 5.0


# ── WaitStepNode ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wait_step_test_mode():
    from app.workflow.steps.wait_step import WaitStepNode
    step = StepDefinition(id="w1", type="wait", duration="1h")
    ctx = ContextResolver()
    node = WaitStepNode(step, ctx)
    state = make_state(is_test_run=True)
    result = await node.execute(state)
    assert result["step_outputs"]["w1"]["waited"] is True


# ── ConditionalStepNode ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_conditional_step_true_branch():
    from app.workflow.steps.conditional_step import ConditionalStepNode
    from app.workflow.dsl import ConditionalBranch
    step = StepDefinition(
        id="c1", type="conditional",
        branches=[
            ConditionalBranch(condition="1 > 0", next="step_a"),
            ConditionalBranch(condition="default", next="step_b"),
        ]
    )
    ctx = ContextResolver()
    node = ConditionalStepNode(step, ctx)
    state = make_state()
    result = await node.execute(state)
    assert result["completed_branch"] == "step_a"


@pytest.mark.asyncio
async def test_conditional_step_default_branch():
    from app.workflow.steps.conditional_step import ConditionalStepNode
    from app.workflow.dsl import ConditionalBranch
    step = StepDefinition(
        id="c2", type="conditional",
        branches=[
            ConditionalBranch(condition="1 > 100", next="step_a"),
            ConditionalBranch(condition="default", next="step_b"),
        ]
    )
    ctx = ContextResolver()
    node = ConditionalStepNode(step, ctx)
    state = make_state()
    result = await node.execute(state)
    assert result["completed_branch"] == "step_b"


# ── EmitEventStepNode ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_event_step_no_redis():
    from app.workflow.steps.emit_event_step import EmitEventStepNode
    step = StepDefinition(id="e1", type="emit_event", event_channel_out="test.channel", event_payload={"key": "val"})
    ctx = ContextResolver()
    node = EmitEventStepNode(step, ctx)
    state = make_state()
    result = await node.execute(state)
    out = result["step_outputs"]["e1"]
    assert out["channel"] == "test.channel"
    assert out["payload"]["key"] == "val"


# ── HTTPStepNode (SSRF) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_step_ssrf_blocked():
    from app.workflow.steps.http_step import HTTPStepNode
    from app.workflow.security import SSRFBlockedError
    step = StepDefinition(id="h1", type="http", url="http://169.254.169.254/", method="GET")
    ctx = ContextResolver()
    node = HTTPStepNode(step, ctx)
    state = make_state()
    with pytest.raises(SSRFBlockedError):
        await node.execute(state)


@pytest.mark.asyncio
async def test_http_step_mock_override():
    from app.workflow.steps.http_step import HTTPStepNode
    step = StepDefinition(id="h2", type="http", url="https://example.com/api", method="POST")
    ctx = ContextResolver()
    node = HTTPStepNode(step, ctx)
    state = make_state(is_test_run=True, mock_overrides={"h2": {"status": "ok"}})
    result = await node.execute(state)
    assert result["step_outputs"]["h2"] == {"status": "ok"}


# ── OcrStepNode (WS-14: OCR as a workflow step, one engine) ────────────────────

import base64 as _b64_ocr

_PNG_OCR = _b64_ocr.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_ocr_step_type_is_registered():
    """The 'ocr' step type is registered (DSL-valid) and maps to OcrStepNode."""
    from app.workflow.registry import StepTypeRegistry
    from app.workflow.steps.ocr_step import OcrStepNode

    assert StepTypeRegistry.is_registered("ocr")
    assert StepTypeRegistry.get("ocr") is OcrStepNode


@pytest.mark.asyncio
async def test_ocr_step_routes_document_through_extract_any():
    """The step calls the one OcrEngine.extract_any and returns its result."""
    from unittest.mock import AsyncMock, MagicMock

    from app.ocr.models import DocumentType, OcrResult
    from app.workflow.steps.ocr_step import OcrStepNode

    engine = MagicMock()
    engine.extract_any = AsyncMock(
        return_value=OcrResult(
            raw_text="INVOICE total 42",
            document_type=DocumentType.INVOICE,
            overall_confidence=0.9,
            page_count=1,
            source_format="image",
        )
    )
    step = StepDefinition(
        id="o1", type="ocr", input={"image_base64": _b64_ocr.b64encode(_PNG_OCR).decode()}
    )
    node = OcrStepNode(step, ContextResolver(), ocr_engine=engine)
    result = await node.execute(make_state())

    engine.extract_any.assert_awaited_once()
    out = result["step_outputs"]["o1"]
    assert out["raw_text"] == "INVOICE total 42"
    assert out["source_format"] == "image"
    assert out["document_type"] == "invoice"


@pytest.mark.asyncio
async def test_ocr_step_missing_input_degrades_not_crashes():
    from app.workflow.steps.ocr_step import OcrStepNode

    step = StepDefinition(id="o2", type="ocr", input={})
    node = OcrStepNode(step, ContextResolver())
    result = await node.execute(make_state())
    out = result["step_outputs"]["o2"]
    assert out["degraded"] is True
    assert out["raw_text"] == ""
