"""Tests for intent_router.py — voice command intent classification.

D-3: classify_intent() — 6 intents, no LLM needed
D-1: Voice-to-Mission — speak → GoalRefinementPipeline → OrgService
D-4: Voice-driven approval → OrgService.record_decision()
"""
from __future__ import annotations

import pytest

from app.voice.intent_router import VoiceIntent, classify_intent

# ── D-3: Intent classification ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("launch a new marketing mission",         VoiceIntent.CREATE_MISSION),
    ("create mission to improve sales pipeline", VoiceIntent.CREATE_MISSION),
    ("start a campaign for Q3 launch",          VoiceIntent.CREATE_MISSION),
    ("I want you to build a data pipeline",     VoiceIntent.CREATE_MISSION),
    ("approve that",                            VoiceIntent.APPROVE),
    ("go ahead with the plan",                  VoiceIntent.APPROVE),
    ("yes, do it",                              VoiceIntent.APPROVE),
    ("reject this request",                     VoiceIntent.REJECT),
    ("no don't",                                VoiceIntent.REJECT),
    ("cancel that",                             VoiceIntent.REJECT),
    ("summarize the week",                      VoiceIntent.SUMMARIZE),
    ("brief me on what happened",               VoiceIntent.SUMMARIZE),
    ("what happened while I was away",          VoiceIntent.SUMMARIZE),
    ("what is the status of missions",          VoiceIntent.STATUS_CHECK),
    ("how is the project going",               VoiceIntent.STATUS_CHECK),
    ("find all active missions",               VoiceIntent.SEARCH),
    ("show me the blocked items",              VoiceIntent.SEARCH),
])
def test_classify_intent(text: str, expected: VoiceIntent):
    result = classify_intent(text)
    assert result.intent == expected, f"classify_intent({text!r}) = {result.intent}, expected {expected}"
    assert 0.0 < result.confidence <= 1.0


def test_classify_intent_unknown():
    result = classify_intent("the quick brown fox")
    assert result.intent == VoiceIntent.UNKNOWN
    assert result.confidence > 0.0


def test_classify_intent_confidence_range():
    """All classifications return confidence in [0, 1]."""
    texts = ["launch mission", "approve", "reject", "summarize", "status", "find", "random text"]
    for text in texts:
        r = classify_intent(text)
        assert 0.0 <= r.confidence <= 1.0, f"Bad confidence {r.confidence} for {text!r}"


@pytest.mark.asyncio
async def test_route_voice_command_unknown_returns_echo():
    """Unknown intent returns echo with confirmation prompt."""
    from app.voice.intent_router import route_voice_command
    response = await route_voice_command(
        "the quick brown fox", org_id="org-1",
        tenant_id="tenant-1", session_factory=None
    )
    assert "heard" in response.lower() or "mission" in response.lower()


@pytest.mark.asyncio
async def test_route_voice_command_summarize_no_factory():
    """Summarize with no session_factory returns graceful fallback."""
    from app.voice.intent_router import route_voice_command
    response = await route_voice_command(
        "summarize my org", org_id="org-1",
        tenant_id="tenant-1", session_factory=None
    )
    # Should return health summary (possibly empty values but no crash)
    assert isinstance(response, str)
    assert len(response) > 0


@pytest.mark.asyncio
async def test_route_voice_command_approve_no_decision():
    """Approve with no pending decision returns informative message."""
    from app.voice.intent_router import route_voice_command
    response = await route_voice_command(
        "approve that", org_id="org-1",
        tenant_id="tenant-1", session_factory=None,
        pending_decision_id=None
    )
    assert "pending" in response.lower() or "decision" in response.lower()
