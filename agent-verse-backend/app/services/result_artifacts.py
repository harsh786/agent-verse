from __future__ import annotations

import ast
import json
from typing import Any

_DOWNLOADS = ["json", "csv", "markdown"]
_JIRA_COLUMNS = [
    {"key": "key", "label": "Key", "type": "link"},
    {"key": "summary", "label": "Summary", "type": "text"},
    {"key": "status", "label": "Status", "type": "badge"},
    {"key": "priority", "label": "Priority", "type": "badge"},
    {"key": "updated", "label": "Updated", "type": "datetime"},
]


def _coerce_output(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return {"text": value}
        return parsed if isinstance(parsed, dict) else {"text": value}
    return {"value": value}


def _tool_name(event: dict[str, Any]) -> str:
    return str(event.get("tool") or event.get("tool_name") or "")


def _artifact_status(status: str, has_rows: bool, tool_success: bool = True) -> str:
    if not tool_success:
        return "failed"
    if status == "complete":
        return "success" if has_rows else "empty"
    return "failed"


def _jira_rows(issues: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        rows.append(
            {
                "key": issue.get("key", ""),
                "summary": issue.get("summary", ""),
                "status": issue.get("status", ""),
                "priority": issue.get("priority", ""),
                "updated": issue.get("updated", ""),
            }
        )
    return rows


def unwrap_event(event: Any) -> dict[str, Any]:
    """The goal event itself, whether flat or in the worker bridge envelope.

    Celery workers publish ``{goal_id, tenant_id, type, payload: <event>}``; the
    API and the event store hand out the event itself. Readers must see both
    the same way (P7-2: an eval read only top-level keys and scored ``""``).
    """
    if not isinstance(event, dict):
        return {"type": "unknown"}
    payload = event.get("payload")
    if isinstance(payload, dict) and payload.get("type", event.get("type")) == event.get("type"):
        merged = dict(payload)
        merged.setdefault("type", event.get("type", ""))
        return merged
    return event


_TERMINAL_ANSWER_KEYS = ("answer", "output", "result", "cited_answer", "summary")


def _answer_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict | list) and value:
        return json.dumps(value)[:4000]
    return ""


def final_answer_text(events: list[dict[str, Any]]) -> str:
    """A goal's final answer: what GET /goals/{id} shows as its text result.

    The terminal ``goal_complete`` event's own answer when it carries one
    (distributed strategies put ``answer`` there), else the last
    ``step_complete`` output. Flat and bridge-wrapped events read the same.
    ``""`` when the goal produced no answer (never a placeholder).
    """
    flat = [unwrap_event(e) for e in events]
    for event in reversed(flat):
        if event.get("type") != "goal_complete":
            continue
        for key in _TERMINAL_ANSWER_KEYS:
            text = _answer_value(event.get(key))
            if text:
                return text
        break
    last_step = next((e for e in reversed(flat) if e.get("type") == "step_complete"), None)
    if last_step is None or last_step.get("output") is None:
        return ""
    return str(last_step["output"]).strip()


def build_result_artifact(goal: str, status: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    events = [unwrap_event(e) for e in events]
    tool_events = [event for event in events if event.get("type") == "tool_call_complete"]
    verification = next(
        (event for event in reversed(events) if event.get("type") == "verification_done"), {}
    )
    jira_events = [event for event in tool_events if _tool_name(event) == "jira_search_issues"]
    jira_event = next(
        (event for event in reversed(jira_events) if event.get("success") is not False),
        jira_events[-1] if jira_events else None,
    )

    if jira_event is not None:
        # Prefer raw structured output (tool_output) over the sanitized string (output).
        # graph.py emits tool_output for structured connector results to avoid
        # truncation causing empty issue counts.
        raw_output = jira_event.get("tool_output")
        output = (
            raw_output if isinstance(raw_output, dict) else _coerce_output(jira_event.get("output"))
        )
        _issues = output.get("issues")
        issues = _issues if isinstance(_issues, list) else []
        rows = _jira_rows(issues)
        issue_word = "issue" if len(rows) == 1 else "issues"
        return {
            "version": 1,
            "kind": "table",
            "title": "Jira issues",
            "summary": f"Found {len(rows)} Jira {issue_word}.",
            "status": _artifact_status(status, bool(rows), jira_event.get("success") is not False),
            "metrics": [
                {"label": "Issues", "value": len(rows)},
                {"label": "Tool calls", "value": len(tool_events)},
            ],
            "tables": [
                {
                    "title": "Issues",
                    "columns": [column.copy() for column in _JIRA_COLUMNS],
                    "rows": rows,
                }
            ],
            "evidence": {
                "tools": [
                    {
                        "name": _tool_name(event),
                        "server_id": event.get("server_id"),
                        "success": event.get("success") is not False,
                    }
                    for event in tool_events
                ],
                "verification": verification.get("reason", ""),
            },
            "downloads": _DOWNLOADS.copy(),
            "debug": {"event_count": len(events)},
        }

    # One answer reader for GET /goals/{id} and the evals (P7-2).
    output = final_answer_text(events) or str(verification.get("reason", "") or "")
    return {
        "version": 1,
        "kind": "text" if output else "empty",
        "title": goal or "Goal result",
        "summary": output or "No structured result was produced.",
        "status": (
            "success" if output and status == "complete" else "empty" if not output else "failed"
        ),
        "metrics": [{"label": "Events", "value": len(events)}],
        "tables": [],
        "evidence": {"tools": [], "verification": verification.get("reason", "")},
        "downloads": ["json", "markdown"],
        "debug": {"event_count": len(events)},
    }
