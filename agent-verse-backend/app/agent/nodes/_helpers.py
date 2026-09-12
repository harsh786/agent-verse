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


def resolve_effective_tool_risk(
    tool_risk: str,
    *,
    autonomy_mode: str,
    connector_auto_approve: bool,
    allow_fa_write_high: bool,
) -> str:
    """Decide the effective risk for a tool call after autonomous-execution opt-ins.

    A ``write_high`` tool is downgraded to ``write_low`` (i.e. executed without a
    human approval gate) only when the user has explicitly opted in — either:

    * the tool's connector is marked ``auto_approve`` (per-connector opt-in), or
    * the run is ``fully-autonomous`` AND the global
      ``ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH`` flag is set.

    Every other risk level (and every non-opted-in ``write_high``) is returned
    unchanged so the default-secure HITL / deny gates still apply.
    """
    if tool_risk != "write_high":
        return tool_risk
    if connector_auto_approve:
        return "write_low"
    if autonomy_mode == "fully-autonomous" and allow_fa_write_high:
        return "write_low"
    return tool_risk


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


def _strip_reasoning(text: str) -> str:
    """Drop a leading reasoning block from a reasoning model's output.

    Reasoning models (Qwen3, DeepSeek-R1, etc.) emit ``<think>…</think>`` before
    the real answer. Everything up to and including the last ``</think>`` is
    discarded. An unterminated ``<think>`` (the model never closed it) is left
    as-is so we don't throw away a partial answer.
    """
    if "</think>" in text:
        return text.rsplit("</think>", 1)[-1].strip()
    return text


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Return the first balanced top-level JSON object in *text*, or None.

    String-aware balanced-brace scan, so braces inside string values don't
    confuse it. Tolerates surrounding prose (a reasoning model that adds a
    sentence around the JSON).
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : j + 1])
                        if isinstance(obj, dict):
                            return obj
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break  # this candidate failed; look for the next '{'
        start = text.find("{", start + 1)
    return None


def _parse_json(text: str, key: str | None = None) -> dict[str, Any]:
    """Extract JSON from LLM text, tolerating code fences and reasoning blocks.

    Order: strip ```json fences and any ``<think>…</think>`` block, try a direct
    parse, then fall back to extracting the first balanced JSON object from the
    surrounding prose. Only when no JSON is recoverable is the raw text returned.
    """
    cleaned = _strip_reasoning(re.sub(r"```(?:json)?\n?", "", text).strip())
    try:
        obj: dict[str, Any] = json.loads(cleaned)
        return obj
    except json.JSONDecodeError:
        extracted = _first_json_object(cleaned)
        if extracted is not None:
            return extracted
        if key == "steps":
            return {"steps": [cleaned]}
        return {"success": True, "reason": cleaned}


def _parse_verifier_response(text: str) -> dict[str, Any]:
    """Parse verifier LLM response — handles JSON, reasoning blocks, and legacy text."""
    clean = _strip_reasoning(re.sub(r"```(?:json)?\n?", "", text).strip())
    try:
        obj: dict[str, Any] = json.loads(clean)
        return obj
    except json.JSONDecodeError:
        pass

    extracted = _first_json_object(clean)
    if extracted is not None and ("success" in extracted or "reason" in extracted):
        return extracted

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
