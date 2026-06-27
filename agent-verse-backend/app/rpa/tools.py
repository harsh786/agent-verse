"""RPA tool metadata exposed to agents and governance layers."""

from __future__ import annotations

from typing import Any, Literal

RPARisk = Literal["read", "low", "high", "unknown"]

RPA_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "rpa_open_url",
        "description": "Open a URL in the active RPA session.",
        "risk": "low",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "rpa_click",
        "description": "Click an element by selector or visible text.",
        "risk": "high",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
        },
    },
    {
        "name": "rpa_type",
        "description": "Type text into an element selected by CSS selector.",
        "risk": "high",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "rpa_extract_text",
        "description": "Extract deterministic text from the page or a selector.",
        "risk": "read",
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
        },
    },
    {
        "name": "rpa_screenshot",
        "description": "Capture a screenshot artifact for the current RPA session.",
        "risk": "read",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    },
    {
        "name": "rpa_wait_for_text",
        "description": "Wait until specific text appears on the page (timeout-based polling).",
        "risk": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "timeout_ms": {"type": "integer"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "rpa_select_option",
        "description": "Select a value from a <select> dropdown element by value or label.",
        "risk": "high",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "rpa_upload_file",
        "description": "Upload a file to an input[type=file] element.",
        "risk": "high",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "file_path": {"type": "string"},
            },
            "required": ["selector", "file_path"],
        },
    },
    {
        "name": "rpa_download_file",
        "description": "Click a download link and save the file as an artifact.",
        "risk": "high",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "rpa_submit_form",
        "description": "Fill multiple form fields and submit the form.",
        "risk": "high",
        "input_schema": {
            "type": "object",
            "properties": {
                "field_values": {
                    "type": "object",
                    "description": "Mapping of CSS selector to value",
                },
                "submit_selector": {"type": "string"},
            },
            "required": ["field_values"],
        },
    },
    # P1.2: CAPTCHA detection, human help, and network-idle tools
    {
        "name": "rpa_detect_captcha",
        "description": (
            "Detect if a CAPTCHA challenge is present on the current page. "
            "Returns 'captcha_detected: true/false'."
        ),
        "category": "browser",
        "risk": "read",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "rpa_request_human_help",
        "description": (
            "Pause the RPA session and request a human operator to take over. "
            "Returns the takeover URL."
        ),
        "category": "browser",
        "risk": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Reason human help is needed (e.g., 'CAPTCHA detected')",
                }
            },
            "required": ["reason"],
        },
    },
    {
        "name": "rpa_wait_for_network_idle",
        "description": (
            "Wait for the page network to be idle (no pending requests). "
            "Useful after form submissions."
        ),
        "category": "browser",
        "risk": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "timeout_ms": {
                    "type": "integer",
                    "default": 10000,
                    "description": "Maximum wait in milliseconds",
                }
            },
            "required": [],
        },
    },
)

_RISK_BY_TOOL = {str(tool["name"]): str(tool["risk"]) for tool in RPA_TOOLS}


def classify_rpa_tool_risk(tool_name: str) -> RPARisk:
    """Classify built-in RPA tools without changing global MCP risk behavior."""

    risk = _RISK_BY_TOOL.get(tool_name, "unknown")
    if risk == "read":
        return "read"
    if risk == "low":
        return "low"
    if risk == "high":
        return "high"
    return "unknown"
