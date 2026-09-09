"""Shared utility functions for the AgentGraph node pipeline.

Pure functions extracted from app.agent.graph — zero semantic changes.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Constants used by helpers
_HIGH_RISK_KEYWORDS = frozenset(
    ("deploy", "delete", "drop", "prod", "production", "destroy", "wipe", "truncate")
)
_RM_COMMAND_PATTERN = re.compile(r"\brm\b")


def _is_high_risk_step(step: str) -> bool:
    lowered = step.lower()
    return any(keyword in lowered for keyword in _HIGH_RISK_KEYWORDS) or bool(
        _RM_COMMAND_PATTERN.search(lowered)
    )


def _guardrail_should_fail_closed(step: str, risk_level: Any = None) -> bool:
    """SAFE-4 (P0-15): decide whether an errored safety check must fail CLOSED.

    A guardrail engine that raises must not be treated as "allowed" on high-risk
    work. Returns True when the run is high risk — either an explicit
    ``_risk_level`` of ``high``/``critical`` in context, or a step whose text
    matches the high-risk keyword heuristic.
    """
    if risk_level is not None and str(risk_level).lower() in {"high", "critical"}:
        return True
    return _is_high_risk_step(step)


def _is_ungrounded_status(status: Any) -> bool:
    """Return True when status equals StepStatus.UNGROUNDED without a hard import."""
    return str(status) == "ungrounded"


def _build_verifier_summary(steps: list) -> str:  # type: ignore[type-arg]
    """Build a rich step summary for the verifier LLM."""

    def _step_line(s: Any) -> str:
        parts = [f"- {getattr(s, 'description', '?')}: {getattr(s, 'output', '')}"]
        if getattr(s, "status", None) is not None:
            from app.agent.state import StepStatus

            if s.status == StepStatus.UNGROUNDED:
                parts.append(
                    "  [UNGROUNDED CLAIM] Step output contains claims not found in tool outputs"
                )
        for tc in getattr(s, "tool_calls", []) or []:
            if not (tc.get("success", True)):
                parts.append(
                    f"  [TOOL FAILED] {tc.get('tool_name', '?')}: {tc.get('error', 'unknown error')}"  # noqa: E501
                )
        if getattr(s, "error", None):
            parts.append(f"  [STEP ERROR] {s.error}")
        return "\n".join(parts)

    failed = [
        s
        for s in steps
        if getattr(s, "error", None)
        or any(not tc.get("success", True) for tc in (getattr(s, "tool_calls", []) or []))
        or (getattr(s, "status", None) is not None and _is_ungrounded_status(s.status))
    ]

    last_five = steps[-5:]
    last_five_ids = {id(s) for s in last_five}
    early_failures = [s for s in failed if id(s) not in last_five_ids]

    parts: list[str] = []
    if early_failures:
        parts.append("FAILED STEPS (occurred before final 5 steps):")
        parts.extend(_step_line(s) for s in early_failures)
        parts.append("")

    if last_five:
        parts.append("MOST RECENT STEPS:")
        parts.extend(_step_line(s) for s in last_five)

    return "\n".join(parts) if parts else "(no steps executed)"


def _parse_json(text: str, key: str | None = None) -> dict[str, Any]:
    """Extract JSON from LLM text, tolerating markdown code-block wrappers."""
    text = re.sub(r"```(?:json)?\n?", "", text).strip()
    try:
        obj: dict[str, Any] = json.loads(text)
        return obj
    except json.JSONDecodeError:
        if key == "steps":
            return {"steps": [text]}
        return {"success": True, "reason": text}


def _parse_verifier_response(text: str) -> dict[str, Any]:
    """Parse verifier LLM response — handles both JSON and legacy text formats."""
    clean = re.sub(r"```(?:json)?\n?", "", text).strip()
    try:
        obj: dict[str, Any] = json.loads(clean)
        return obj
    except json.JSONDecodeError:
        pass

    upper = clean.upper()
    if upper.startswith("SUCCESS"):
        reason = re.sub(r"^SUCCESS\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        return {"success": True, "reason": reason, "retry": False}
    elif upper.startswith("RETRY"):
        reason = re.sub(r"^RETRY\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        return {"success": False, "reason": reason, "retry": True}
    elif upper.startswith("FAIL"):
        reason = re.sub(r"^FAIL\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        return {"success": False, "reason": reason, "retry": False}

    lower = clean.lower()
    inferred_success = not any(
        w in lower for w in ["fail", "error", "not ", "missing", "incomplete", "retry"]
    )
    return {"success": inferred_success, "reason": clean}


def _extract_tool_name(step: str) -> str:
    """Heuristically extract a tool name from a step description."""
    lower = step.lower()
    if "call" in lower:
        parts = lower.split("call", 1)
        if len(parts) > 1:
            words = parts[1].strip().split()
            if words:
                return words[0].strip("_-.,;:")
    return "llm_call"


def _extract_scope_value(step: str) -> str | None:
    """Extract a repository / project / resource name from a step description."""
    github_match = re.search(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", step)
    if github_match:
        return github_match.group(1)
    jira_match = re.search(r"\b([A-Z]{2,10})-\d+\b", step)
    if jira_match:
        return jira_match.group(1)
    return None
