"""Consent governance for the voice subsystem (D-24, Coverage-Matrix row 21).

Raw audio and transcripts are personal data. Before a streaming session processes
any audio we require a *recorded* consent flag for the ``(tenant, speaker)`` pair.
When consent is absent the policy fails **closed** — it refuses to process rather
than silently transcribing.

The policy is a small in-memory store by design: a streaming session is short-lived
and single-process, and consent for it is captured over the WebSocket protocol (an
initial ``consent`` message, or the ``consent_granted`` session-config flag). A
DB-backed consent ledger already exists for GDPR/DPDP (``consent_records`` /
``dpdp_consents``); wiring this gate to that ledger is a follow-up — see the module
TODO — and is intentionally out of scope for the voice package.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

# TODO(D-24): persist voice consent to the shared consent ledger
# (app/enterprise/compliance_v2.py -> consent_records / dpdp_consents) and load it
# in app/main.py lifespan so consent survives across replicas. The in-memory policy
# below is correct and fail-closed for a single streaming session.


class VoiceConsentError(Exception):
    """Raised when audio processing is attempted without recorded consent."""

    code = "consent_required"

    def __init__(self, message: str = "voice consent required") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class ConsentRecord:
    """An immutable record that a speaker consented (or declined) to processing."""

    tenant_id: str
    speaker_id: str
    granted: bool
    purpose: str = "voice_streaming"
    recorded_at: _dt.datetime = field(
        default_factory=lambda: _dt.datetime.now(_dt.UTC)
    )


class VoiceConsentPolicy:
    """Records and enforces per-``(tenant, speaker)`` voice-processing consent.

    Args:
        fail_closed: When ``True`` (default) a missing record means *no consent* and
            :meth:`require` raises. When ``False`` the policy is permissive (used only
            where consent is enforced upstream) and treats any speaker as consented.
    """

    def __init__(self, *, fail_closed: bool = True) -> None:
        self.fail_closed = fail_closed
        self._records: dict[tuple[str, str], ConsentRecord] = {}

    def record_consent(
        self,
        tenant_id: str,
        speaker_id: str,
        *,
        granted: bool = True,
        purpose: str = "voice_streaming",
    ) -> ConsentRecord:
        """Record (or overwrite) a consent decision and return the stored record."""
        rec = ConsentRecord(
            tenant_id=tenant_id,
            speaker_id=speaker_id,
            granted=granted,
            purpose=purpose,
        )
        self._records[(tenant_id, speaker_id)] = rec
        return rec

    def revoke(self, tenant_id: str, speaker_id: str) -> None:
        """Remove any recorded consent for the pair (idempotent)."""
        self._records.pop((tenant_id, speaker_id), None)

    def has_consent(self, tenant_id: str, speaker_id: str) -> bool:
        """Return whether the speaker has an active *granted* consent record."""
        rec = self._records.get((tenant_id, speaker_id))
        if rec is not None:
            return rec.granted
        return not self.fail_closed

    def require(self, tenant_id: str, speaker_id: str) -> None:
        """Raise :class:`VoiceConsentError` unless consent is on record."""
        if not self.has_consent(tenant_id, speaker_id):
            raise VoiceConsentError(
                f"no voice-processing consent for speaker {speaker_id!r}"
            )
