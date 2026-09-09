"""Tests for greeting.py — login greeting builder.

VERIFIED API calls in greeting.py:
  - OrgService.get_org_health() EXISTS at service.py:844
  - DigestGenerator.generate() EXISTS at org/digest.py:131
  - jurisdiction_to_language() pure function
"""
from __future__ import annotations

import pytest

MOCK_HEALTH_HEALTHY = {
    "org_id":                    "org-1",
    "org_name":                  "Acme AI",
    "overall_health":            "healthy",
    "active_missions":           3,
    "active_teams":              2,
    "pending_approvals":         1,
    "items_needing_attention":   0,
}
MOCK_HEALTH_DEGRADED = {
    **MOCK_HEALTH_HEALTHY,
    "overall_health":          "degraded",
    "items_needing_attention": 4,
}
MOCK_HEALTH_ATTENTION = {
    **MOCK_HEALTH_HEALTHY,
    "overall_health":          "attention_needed",
    "items_needing_attention": 7,
}


@pytest.mark.asyncio
async def test_build_greeting_healthy():
    """Healthy org produces greeting with mission count and user name."""
    from app.voice.greeting import build_greeting_script
    script = await build_greeting_script(MOCK_HEALTH_HEALTHY, "Harsh Kumar")
    assert "Harsh" in script
    assert "Acme AI" in script
    assert "3" in script        # active_missions


@pytest.mark.asyncio
async def test_build_greeting_degraded():
    """Degraded org includes attention warning."""
    from app.voice.greeting import build_greeting_script
    script = await build_greeting_script(MOCK_HEALTH_DEGRADED, "Ada")
    assert "Ada" in script
    assert "4" in script        # items_needing_attention


@pytest.mark.asyncio
async def test_build_greeting_attention_needed():
    """Attention needed org uses urgent template."""
    from app.voice.greeting import build_greeting_script
    script = await build_greeting_script(MOCK_HEALTH_ATTENTION, "Bob")
    assert "Bob" in script
    assert "7" in script


@pytest.mark.asyncio
async def test_build_greeting_wywa():
    """WYWA items are narrated in greeting."""
    from app.voice.greeting import build_greeting_script
    script = await build_greeting_script(
        MOCK_HEALTH_HEALTHY, "Alice",
        wywa_items=5, wywa_summary="5 updates while you were away."
    )
    assert "5 updates" in script


@pytest.mark.asyncio
async def test_build_greeting_time_of_day_morning(monkeypatch):
    """Greeting says 'morning' for AM hours."""
    import datetime as _dt

    from app.voice import greeting as gmod

    class _FakeDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):   # type: ignore[override]
            return cls(2026, 8, 20, 9, 0, tzinfo=_dt.timezone.utc)

    monkeypatch.setattr(gmod.datetime, "datetime", _FakeDatetime)
    script = await gmod.build_greeting_script(MOCK_HEALTH_HEALTHY, "Test")
    assert "morning" in script.lower()


@pytest.mark.asyncio
async def test_build_greeting_time_of_day_evening(monkeypatch):
    """Greeting says 'evening' for PM hours."""
    import datetime as _dt

    from app.voice import greeting as gmod

    class _FakeDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):   # type: ignore[override]
            return cls(2026, 8, 20, 20, 0, tzinfo=_dt.timezone.utc)

    monkeypatch.setattr(gmod.datetime, "datetime", _FakeDatetime)
    script = await gmod.build_greeting_script(MOCK_HEALTH_HEALTHY, "Test")
    assert "evening" in script.lower()


def test_jurisdiction_to_language_english():
    from app.voice.greeting import jurisdiction_to_language
    assert jurisdiction_to_language("USA") == "en"
    assert jurisdiction_to_language("United States") == "en"
    assert jurisdiction_to_language(None) == "en"
    assert jurisdiction_to_language("") == "en"


def test_jurisdiction_to_language_other():
    from app.voice.greeting import jurisdiction_to_language
    assert jurisdiction_to_language("India") == "hi"
    assert jurisdiction_to_language("France") == "fr"
    assert jurisdiction_to_language("Japan") == "ja"
    assert jurisdiction_to_language("Brazil") == "pt"
    assert jurisdiction_to_language("Germany") == "de"
    assert jurisdiction_to_language("Saudi Arabia") == "ar"
    assert jurisdiction_to_language("UAE") == "ar"


@pytest.mark.asyncio
async def test_synthesize_greeting_returns_wav():
    """synthesize_greeting() returns WAV bytes using mock TTS."""
    import os
    os.environ['VOICE_TTS_PROVIDER'] = 'browser'
    from app.voice.providers import reset_providers
    reset_providers()
    from app.voice.greeting import synthesize_greeting
    wav = await synthesize_greeting(MOCK_HEALTH_HEALTHY, "Harsh")
    assert isinstance(wav, bytes)
    assert len(wav) > 44   # at least WAV header
    reset_providers()
