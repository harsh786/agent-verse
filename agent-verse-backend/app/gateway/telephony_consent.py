"""SMS / voice consent ledger — default DENY for outbound messaging and calls.

Before this module there was no consent state at all for telephony: an agent
could text or phone any number (``twilio_send_sms`` / ``twilio_make_call`` /
``VoicePhoneChannelAdapter.place_call``), and an inbound "STOP" was ingested as
an ordinary message for the agent to act on. Messaging a number that never
opted in — or that replied STOP — is a TCPA / carrier-policy violation.

Rules enforced here:

* Outbound SMS / WhatsApp / calls require an explicit, recorded opt-in for the
  ``(tenant, number)`` pair. Unknown state means **no** (fail closed).
* The industry-standard keywords on an inbound SMS update the ledger: STOP-family
  keywords record an opt-out, START-family keywords record an opt-in.
* ``tenant_id == ""`` is the platform-level Twilio account (the env-configured
  ``TWILIO_*`` credentials the built-in MCP server uses, which carries no tenant).

LIMITATION: the ledger is process-local (like ``app.voice.consent``). Opt-outs
are not yet shared across replicas; persisting to the shared consent ledger is
tracked alongside D-24 in ``app/voice/consent.py``.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

__all__ = [
    "OPT_IN_KEYWORDS",
    "OPT_OUT_KEYWORDS",
    "PLATFORM_TENANT",
    "TelephonyConsentError",
    "TelephonyConsentLedger",
    "get_telephony_consent_ledger",
    "normalize_number",
]

PLATFORM_TENANT = ""

# Twilio's default opt-out / opt-in keyword sets (case-insensitive, whole body).
OPT_OUT_KEYWORDS = frozenset(
    {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
)
OPT_IN_KEYWORDS = frozenset({"START", "YES", "UNSTOP"})


class TelephonyConsentError(PermissionError):
    """Raised when an outbound SMS / call targets a number without recorded consent."""

    code = "telephony_consent_required"


def normalize_number(number: str) -> str:
    """Canonical key for a phone number: drop channel prefixes and formatting."""
    raw = (number or "").strip()
    if ":" in raw:  # "whatsapp:+1555...", "sms:+1555..."
        raw = raw.split(":", 1)[1]
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if digits else ""


@dataclass(frozen=True)
class ConsentState:
    granted: bool
    source: str
    recorded_at: _dt.datetime = field(default_factory=lambda: _dt.datetime.now(_dt.UTC))


class TelephonyConsentLedger:
    """Per-``(tenant, number)`` opt-in / opt-out record. Unknown → deny."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ConsentState] = {}

    def record(self, tenant_id: str, number: str, *, granted: bool, source: str) -> None:
        key = normalize_number(number)
        if not key:
            return
        self._records[(tenant_id, key)] = ConsentState(granted=granted, source=source)

    def status(self, tenant_id: str, number: str) -> bool | None:
        rec = self._records.get((tenant_id, normalize_number(number)))
        return None if rec is None else rec.granted

    def allows_outbound(self, tenant_id: str, number: str) -> bool:
        # Only an explicit opt-in allows; unknown (None) and opt-out both deny.
        return self.status(tenant_id, number) is True

    def require_outbound(self, tenant_id: str, number: str) -> None:
        if not self.allows_outbound(tenant_id, number):
            raise TelephonyConsentError(
                f"no recorded SMS/voice consent for {normalize_number(number) or number!r}"
            )

    def apply_inbound_keyword(self, tenant_id: str, number: str, body: str) -> str | None:
        """Apply a STOP / START keyword from an inbound SMS.

        Returns ``"opt_out"`` / ``"opt_in"`` when the body was a keyword (and the
        ledger was updated), else ``None``.
        """
        word = (body or "").strip().upper()
        if word in OPT_OUT_KEYWORDS:
            self.record(tenant_id, number, granted=False, source="sms_keyword")
            return "opt_out"
        if word in OPT_IN_KEYWORDS:
            self.record(tenant_id, number, granted=True, source="sms_keyword")
            return "opt_in"
        return None


_DEFAULT_LEDGER = TelephonyConsentLedger()


def get_telephony_consent_ledger() -> TelephonyConsentLedger:
    """The process-wide ledger (used by callers without an injected one)."""
    return _DEFAULT_LEDGER
