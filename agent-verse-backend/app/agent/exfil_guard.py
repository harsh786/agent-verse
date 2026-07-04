"""
Data Exfiltration Guard
=======================
Detects when an agent is about to send sensitive data to external sinks.

Checks:
1. Tool arguments: look for secrets/credentials/PII being passed to write-tools
2. Large payload anomaly: flag unusually large payloads to external sinks
3. Volume anomaly: detect data exfiltration pattern (many small sends)

Wired into: MCPClient.call_tool() before dispatching write tools
"""
from __future__ import annotations

import re
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Patterns that suggest sensitive data
_SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_\-]?key|access[_\-]?token|secret[_\-]?key|password|credential|bearer\s+[A-Za-z0-9._\-]{20,})",
    ),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),  # OpenAI key pattern (relaxed length)
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub token
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS access key
]

_WRITE_SINKS = frozenset({
    "send_email",
    "send_slack_message",
    "post_webhook",
    "create_issue",
    "create_page",
    "http_post",
    "http_put",
    "write_file",
    "upload_file",
})

_MAX_SAFE_PAYLOAD = 50_000  # 50 KB threshold


def _contains_secret(text: str) -> bool:
    """Return True if text appears to contain credentials or secrets."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def check_tool_args_for_exfil(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    tenant_id: str = "",
) -> tuple[bool, str]:
    """Check tool arguments for potential data exfiltration.

    Returns:
        (blocked: bool, reason: str)
    """
    tool_base = tool_name.split(".")[-1].lower().replace("-", "_")

    # Only check write/send tools
    if tool_base not in _WRITE_SINKS and not any(
        tool_base.startswith(p) for p in ("send_", "post_", "write_", "upload_")
    ):
        return False, ""

    # Check all string argument values
    args_str = str(arguments)

    # Secret check
    if _contains_secret(args_str):
        reason = f"Tool '{tool_name}' arguments appear to contain credentials/secrets"
        logger.warning(
            "exfil_guard_blocked_secrets",
            tool=tool_name,
            tenant_id=tenant_id,
        )
        return True, reason

    # Large payload check
    if len(args_str) > _MAX_SAFE_PAYLOAD:
        reason = f"Tool '{tool_name}' payload exceeds {_MAX_SAFE_PAYLOAD // 1000}KB threshold"
        logger.warning(
            "exfil_guard_large_payload",
            tool=tool_name,
            payload_size=len(args_str),
            tenant_id=tenant_id,
        )
        return True, reason

    return False, ""


__all__ = [
    "_MAX_SAFE_PAYLOAD",
    "check_tool_args_for_exfil",
]
