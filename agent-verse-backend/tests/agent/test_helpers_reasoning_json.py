"""Reasoning-model-aware JSON parsing in the agent node helpers.

Reasoning models (Qwen3, DeepSeek-R1, …) wrap answers in <think>…</think> and
sometimes surround the JSON with prose. The planner/verifier parsers must still
recover the structured object instead of treating the whole blob as one step.
"""
from __future__ import annotations

from app.agent.nodes._helpers import (
    _first_json_object,
    _parse_json,
    _parse_verifier_response,
    _strip_reasoning,
)


def test_strip_reasoning_drops_think_block() -> None:
    assert _strip_reasoning("<think>reasoning here</think>\n{\"a\": 1}") == '{"a": 1}'
    # Unterminated think is kept as-is (don't discard a partial answer).
    assert _strip_reasoning("<think>still thinking") == "<think>still thinking"
    assert _strip_reasoning("no think block") == "no think block"


def test_first_json_object_ignores_braces_in_strings() -> None:
    assert _first_json_object('{"k": "a } b", "n": {"x": 1}}') == {"k": "a } b", "n": {"x": 1}}
    assert _first_json_object("no json here") is None


def test_parse_json_recovers_steps_from_think_wrapped_plan() -> None:
    # This is exactly what the self-hosted Qwen3.5-4B produced.
    raw = (
        "<think>The user wants a plan. I will break it into three steps.</think>\n"
        '{\n  "steps": [\n    "Add the prices to get the subtotal.",\n'
        '    "Apply a 10% discount.",\n    "State the final amount."\n  ]\n}'
    )
    parsed = _parse_json(raw, key="steps")
    assert parsed["steps"] == [
        "Add the prices to get the subtotal.",
        "Apply a 10% discount.",
        "State the final amount.",
    ]
    # The whole blob is NOT collapsed into a single raw step anymore.
    assert len(parsed["steps"]) == 3


def test_parse_json_recovers_from_prose_wrapped_json() -> None:
    raw = 'Sure, here is the plan: {"steps": ["do X", "do Y"]} — hope that helps!'
    assert _parse_json(raw, key="steps")["steps"] == ["do X", "do Y"]


def test_parse_json_plain_still_works() -> None:
    assert _parse_json('{"steps": ["only"]}', key="steps")["steps"] == ["only"]


def test_parse_json_no_json_falls_back_to_raw_step() -> None:
    parsed = _parse_json("just prose, no json", key="steps")
    assert parsed["steps"] == ["just prose, no json"]


def test_parse_verifier_recovers_json_from_think_block() -> None:
    raw = (
        "<think>Let me check the math: 12.50 + 8.99 + 15.00 = 36.49, minus 10% = 32.84.</think>\n"
        '{"success": true, "reason": "subtotal 36.49, final 32.84"}'
    )
    result = _parse_verifier_response(raw)
    assert result["success"] is True
    assert "32.84" in result["reason"]


def test_parse_verifier_legacy_text_still_works() -> None:
    assert _parse_verifier_response("SUCCESS: all good")["success"] is True
    assert _parse_verifier_response("FAIL: missing output")["success"] is False
