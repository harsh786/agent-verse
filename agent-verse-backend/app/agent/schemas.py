"""
Pydantic response schemas for structured LLM output.

Used by:
  - _node_plan: planner must emit a PlannerPlan JSON
  - _node_verify: verifier must emit a VerifierVerdict JSON

When the provider supports structured outputs (Anthropic force-tool,
OpenAI json_schema, Gemini response_schema), these schemas are passed
as `CompletionRequest.response_schema` so the provider enforces the format.

Falls back to the existing text-parsing path when structured outputs
are not supported or fail.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    step: str
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class PlannerPlan(BaseModel):
    steps: list[str]  # simple steps list (matches existing _parse_json("steps") output)
    reasoning: str | None = None


class VerifierVerdict(BaseModel):
    success: bool
    reason: str
    retry: bool = True
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    ungrounded_claims: list[str] = Field(default_factory=list)


def planner_schema() -> dict[str, Any]:
    """Return the JSON Schema for PlannerPlan (passed as response_schema)."""
    return {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "reasoning": {"type": "string"},
        },
        "required": ["steps"],
        "additionalProperties": False,
    }


def verifier_schema() -> dict[str, Any]:
    """Return the JSON Schema for VerifierVerdict (passed as response_schema)."""
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "reason": {"type": "string"},
            "retry": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "ungrounded_claims": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["success", "reason"],
        "additionalProperties": False,
    }


def parse_verifier_verdict(raw: str) -> dict[str, Any]:
    """
    Parse verifier response into a VerifierVerdict-compatible dict.
    Tries JSON parse first, falls back to text heuristics.
    """
    raw = raw.strip()

    # Try direct JSON parse
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "success" in data:
            return {
                "success": bool(data.get("success", False)),
                "reason": str(data.get("reason", "")),
                "retry": bool(data.get("retry", True)),
                "confidence": float(data.get("confidence", 0.8)),
                "ungrounded_claims": data.get("ungrounded_claims", []),
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Heuristic: look for JSON block in markdown
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            return {
                "success": bool(data.get("success", False)),
                "reason": str(data.get("reason", "")),
                "retry": bool(data.get("retry", True)),
                "confidence": float(data.get("confidence", 0.8)),
                "ungrounded_claims": data.get("ungrounded_claims", []),
            }
        except Exception:
            pass

    # Text fallback
    lower = raw.lower()
    success = any(
        kw in lower
        for kw in ("success: true", '"success": true', "goal achieved", "completed successfully")
    )
    failure = any(
        kw in lower
        for kw in (
            "success: false",
            '"success": false',
            "not achieved",
            "failed",
            "incomplete",
        )
    )

    return {
        "success": success and not failure,
        "reason": raw[:500],
        "retry": True,
        "confidence": 0.5,
        "ungrounded_claims": [],
    }
