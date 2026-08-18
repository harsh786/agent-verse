"""Tests for QualityGateSystem — app/org/quality_gates.py"""
from __future__ import annotations

import pytest
from app.org.quality_gates import QualityGateSystem, GateResult


@pytest.mark.asyncio
async def test_quality_gates_pass_good_output():
    qg = QualityGateSystem()
    result = await qg.evaluate(
        "This is a detailed analysis of the German market with 500 words of content providing comprehensive insights.",
        context={"output_type": "text"},
    )
    assert result.final_score > 0.60
    assert result.decision in ("auto_approve", "promote", "human_review")


@pytest.mark.asyncio
async def test_quality_gates_fail_empty_output():
    qg = QualityGateSystem()
    result = await qg.evaluate("", context={})
    assert result.final_score < 0.50
    assert result.decision in ("reject", "human_review")


@pytest.mark.asyncio
async def test_quality_gates_detect_pii():
    qg = QualityGateSystem()
    result = await qg.evaluate(
        "The user's SSN is 123-45-6789 and email is test@example.com",
        context={},
    )
    # Gate 5 (policy) fails → low score
    gate5 = next((g for g in result.gates if g.gate_id == 5), None)
    assert gate5 is not None
    assert gate5.result == GateResult.FAIL


@pytest.mark.asyncio
async def test_quality_gates_auto_approve_high_score():
    qg = QualityGateSystem()
    # Craft output that passes all heuristic checks
    long_output = " ".join(["comprehensive analysis"] * 100)  # 200 words
    result = await qg.evaluate(long_output, context={"output_type": "text"})
    assert result.final_score >= 0.75


@pytest.mark.asyncio
async def test_quality_gates_valid_json():
    qg = QualityGateSystem()
    result = await qg.evaluate(
        '{"analysis": "Market is growing", "confidence": 0.85}',
        context={"output_type": "json"},
    )
    # Gate 2 passes for valid JSON
    gate2 = next((g for g in result.gates if g.gate_id == 2), None)
    assert gate2 is not None
    assert gate2.result == GateResult.PASS


@pytest.mark.asyncio
async def test_quality_gates_invalid_json_fails_gate2():
    qg = QualityGateSystem()
    result = await qg.evaluate("{invalid json}", context={"output_type": "json"})
    gate2 = next((g for g in result.gates if g.gate_id == 2), None)
    assert gate2 is not None
    assert gate2.result == GateResult.FAIL


@pytest.mark.asyncio
async def test_quality_gates_human_approval_required():
    qg = QualityGateSystem(require_human_approval=True)
    result = await qg.evaluate("Good output", context={})
    # Human approval always required
    assert result.decision == "human_review"
