"""Twilio ``X-Twilio-Signature`` verification — fail closed.

Twilio signs ``base64(HMAC-SHA1(auth_token, url + concat(sorted k+v)))`` over the
full request URL and the POSTed form params. Mirrors the repo convention in
``app/integrations/webhook_auth.py``: an unconfigured token is 503 (never "open
in dev"), a missing / wrong signature is 403.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

__all__ = ["compute_twilio_signature", "require_twilio_signature", "twilio_signature_valid"]


def compute_twilio_signature(auth_token: str, url: str, params: Mapping[str, Any]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def twilio_signature_valid(
    auth_token: str, url: str, params: Mapping[str, Any], signature: str
) -> bool:
    """False for an empty token or signature — never "valid by default"."""
    if not auth_token or not signature:
        return False
    try:
        expected = compute_twilio_signature(auth_token, url, params)
    except Exception:  # malformed params → reject, never accept
        return False
    return hmac.compare_digest(signature, expected)


def require_twilio_signature(
    auth_token: str,
    *,
    url: str,
    params: Mapping[str, Any],
    signature: str,
    label: str,
    env_var: str,
) -> None:
    if not auth_token:
        raise HTTPException(503, f"{label} webhook is not configured ({env_var} is unset)")
    if not twilio_signature_valid(auth_token, url, params, signature):
        raise HTTPException(403, f"Invalid {label} signature")
