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


def build_result_artifact(goal: str, status: str, events: list[dict[str, Any]]) -> dict[str, Any]:
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
        output = raw_output if isinstance(raw_output, dict) else _coerce_output(jira_event.get("output"))
        issues = output.get("issues") if isinstance(output.get("issues"), list) else []
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

    last_step = next(
        (event for event in reversed(events) if event.get("type") == "step_complete"), {}
    )
    output_value = last_step["output"] if "output" in last_step else verification.get("reason", "")
    output = str(output_value) if output_value is not None else ""
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
