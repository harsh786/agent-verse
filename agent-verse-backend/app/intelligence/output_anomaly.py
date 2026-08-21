"""
Output Anomaly Detection
=========================
Detects statistical anomalies in LLM outputs that may indicate:
- Data exfiltration (output 10x longer than expected)
- Credential leakage (API keys, tokens in output)
- Unusual repetition (sign of prompt injection loop)
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

try:
    from app.observability.logging import get_logger

    logger = get_logger(__name__)
except Exception:
    import logging

    logger = logging.getLogger(__name__)  # type: ignore[assignment]

_SECRET_PATTERNS = [
    re.compile(r"(sk|pk|rk)_[a-zA-Z0-9_]{20,}"),  # Stripe/generic keys
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*[\"']?[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub tokens
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS keys
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9._\-]{30,}"),  # Bearer tokens
    re.compile(r"(?i)password\s*[=:]\s*[\"']?[^\s\"']{8,}"),  # Passwords
    re.compile(r"-----BEGIN\s+(?:RSA\s+)?(?:EC\s+)?PRIVATE\s+KEY-----"),  # Private keys
]

_MAX_NORMAL_OUTPUT_CHARS = 50000  # 50KB is suspicious
_MAX_REPETITION_RATIO = 0.7  # if 70%+ of output is repeated content


def scan_output_for_anomalies(
    output: str,
    *,
    context_length: int = 0,
    expected_max_length: int = _MAX_NORMAL_OUTPUT_CHARS,
) -> dict[str, Any]:
    """
    Scan LLM output for anomalies.
    Returns dict: {clean, anomalies, severity}
    """
    anomalies: list[str] = []
    severity = "low"

    if not output:
        return {"clean": True, "anomalies": [], "severity": "low"}

    # 1. Secret/credential detection
    for pattern in _SECRET_PATTERNS:
        m = pattern.search(output)
        if m:
            redacted = m.group(0)[:5] + "..." + m.group(0)[-3:]
            anomalies.append(f"Potential credential in output: {redacted}")
            severity = "critical"

    # 2. Output size anomaly
    if len(output) > expected_max_length:
        anomalies.append(
            f"Output size anomaly: {len(output)} chars (expected <= {expected_max_length})"
        )
        if len(output) > expected_max_length * 3:
            severity = "high" if severity == "low" else severity

    # 3. Suspicious repetition (injection loop sign)
    words = output.split()
    if len(words) > 50:
        unique_words = set(words)
        repetition_ratio = 1 - len(unique_words) / len(words)
        if repetition_ratio > _MAX_REPETITION_RATIO:
            anomalies.append(f"High repetition ratio in output: {repetition_ratio:.1%}")
            severity = "medium" if severity == "low" else severity

    if anomalies:
        with contextlib.suppress(Exception):
            logger.warning("output_anomaly_detected", anomalies=anomalies, severity=severity)

    return {
        "clean": len(anomalies) == 0,
        "anomalies": anomalies,
        "severity": severity,
    }
