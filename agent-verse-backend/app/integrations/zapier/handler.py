"""Zapier webhook integration.

Allows Zapier to trigger AgentVerse goals and receive results.
Compatible with Zapier's webhook trigger and action patterns.
"""
from __future__ import annotations

import hmac
import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def get_zapier_secret() -> str:
    return os.getenv("ZAPIER_WEBHOOK_SECRET", "")


def verify_zapier_secret(secret_header: str) -> bool:
    expected = get_zapier_secret()
    if not expected:
        if os.getenv("ENVIRONMENT", "development") == "production":
            return False  # Fail-closed in production
        return True  # Allow in development only
    return hmac.compare_digest(expected, secret_header)


def map_zapier_payload_to_goal(payload: dict[str, Any]) -> str:
    """Extract goal text from Zapier webhook payload."""
    # Support multiple Zapier payload formats
    if "goal" in payload:
        return str(payload["goal"])
    if "text" in payload:
        return str(payload["text"])
    if "message" in payload:
        return str(payload["message"])
    # Fallback: stringify the whole payload
    return str(payload)[:500]
