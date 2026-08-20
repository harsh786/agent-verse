"""Voice greeting builder — synthesised on every org page load.

Data sources (VERIFIED against actual codebase):
  1. OrgService.get_org_health(org_id)   ← EXISTS at service.py:844
  2. DigestGenerator.generate()           ← EXISTS at app/org/digest.py:131
  3. Organization.jurisdiction            ← D-5: auto-selects TTS language (600+ via OmniVoice)

D-2: "While You Were Away" digest narration uses real DigestGenerator.
D-5: Multi-language by org jurisdiction.
D-7: Real org health data in greeting.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

import structlog
from opentelemetry import trace

log    = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── D-5: Jurisdiction → TTS language map ─────────────────────────────────────
JURISDICTION_TO_LANG: dict[str, str] = {
    "india": "hi", "in": "hi",
    "france": "fr", "fr": "fr",
    "germany": "de", "de": "de",
    "spain": "es", "es": "es",
    "japan": "ja", "jp": "ja",
    "china": "zh", "cn": "zh",
    "brazil": "pt", "br": "pt",
    "korea": "ko", "kr": "ko",
    "italy": "it", "it": "it",
    "russia": "ru", "ru": "ru",
    "arab": "ar", "uae": "ar", "sa": "ar",
}

_TEMPLATES: dict[str, str] = {
    "healthy": (
        "Good {tod}, {first_name}. "
        "{org_name} is operating smoothly. "
        "You have {active_missions} active mission{m_pl} and {active_teams} team{t_pl} engaged. "
        "{pending_approvals} approval{pa_pl} awaiting your review. "
        "{wywa_summary}"
    ),
    "degraded": (
        "Good {tod}, {first_name}. "
        "Heads up — {org_name} has {items} item{items_pl} needing attention. "
        "{active_missions} mission{m_pl} running across {active_teams} team{t_pl}. "
        "{pending_approvals} approval{pa_pl} pending. "
        "{wywa_summary}"
    ),
    "attention_needed": (
        "Good {tod}, {first_name}. "
        "{org_name} requires your immediate attention. "
        "{items} critical issue{items_pl} flagged. "
        "{active_missions} mission{m_pl} in flight. "
        "{wywa_summary}"
    ),
}


async def build_greeting_script(
    health: dict[str, Any],
    user_name: str,
    *,
    wywa_items: int = 0,
    wywa_summary: str = "",
) -> str:
    """Render a natural-language greeting from OrgService.get_org_health() data.

    Args:
        health:      dict from OrgService.get_org_health() — keys:
                     org_id, org_name, overall_health, active_missions, active_teams,
                     pending_approvals, items_needing_attention
        user_name:   Full name of the logged-in user.
        wywa_items:  Count from DigestGenerator (D-2 while-you-were-away).
        wywa_summary: Short WYWA text if available.
    """
    hour = datetime.datetime.now(datetime.timezone.utc).hour
    if hour < 12:
        tod = "morning"
    elif hour < 17:
        tod = "afternoon"
    else:
        tod = "evening"

    health_status     = health.get("overall_health", "healthy")
    template          = _TEMPLATES.get(health_status, _TEMPLATES["healthy"])
    first_name        = (user_name or "there").split()[0]
    active_missions   = int(health.get("active_missions", 0))
    active_teams      = int(health.get("active_teams", 0))
    pending_approvals = int(health.get("pending_approvals", 0))
    items             = int(health.get("items_needing_attention", 0))

    if wywa_items > 0 and not wywa_summary:
        wywa_summary = f"{wywa_items} update{'s' if wywa_items != 1 else ''} happened while you were away."

    return template.format(
        tod               = tod,
        first_name        = first_name,
        org_name          = health.get("org_name", "your organisation"),
        active_missions   = active_missions,
        m_pl              = "s" if active_missions != 1 else "",
        active_teams      = active_teams,
        t_pl              = "s" if active_teams != 1 else "",
        pending_approvals = pending_approvals,
        pa_pl             = "s" if pending_approvals != 1 else "",
        items             = items,
        items_pl          = "s" if items != 1 else "",
        wywa_summary      = wywa_summary,
    )


def jurisdiction_to_language(jurisdiction: str | None) -> str:
    """D-5: Auto-detect TTS language from org jurisdiction field."""
    if not jurisdiction:
        return "en"
    j = jurisdiction.lower().strip()
    # Use startswith or exact match to avoid substring false-positives (e.g. 'usa' matching 'sa')
    for key, lang in JURISDICTION_TO_LANG.items():
        k = key.lower()
        # Exact match or whole-word match
        if j == k or j.startswith(k + ' ') or j.endswith(' ' + k) or f' {k} ' in f' {j} ':
            return lang
    return "en"


async def synthesize_greeting(
    health: dict[str, Any],
    user_name: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None   = None,
    language: str          = "en",
    wywa_items: int        = 0,
    wywa_summary: str      = "",
) -> bytes:
    """Return WAV bytes for the login greeting using real org health data."""
    with tracer.start_as_current_span("voice.greeting.synthesize") as span:
        span.set_attribute("org_id", health.get("org_id", ""))
        span.set_attribute("health", health.get("overall_health", ""))

        script = await build_greeting_script(
            health, user_name, wywa_items=wywa_items, wywa_summary=wywa_summary,
        )
        span.set_attribute("script_len", len(script))

        from app.voice.tts_engine import synthesize
        wav = await synthesize(script, ref_audio=ref_audio, ref_text=ref_text, language=language)
        log.info("voice.greeting.synthesized",
                 org_id=health.get("org_id"), health=health.get("overall_health"),
                 wywa_items=wywa_items, wav_bytes=len(wav))
        return wav
