"""Unit tests verifying all 6 hallucination-elimination fixes."""
import pytest


# ── Vector 5: Grounded executor prompt ───────────────────────────────────────

def test_executor_system_contains_grounding_rules():
    """EXECUTOR_SYSTEM must contain all 5 grounding rules."""
    from app.agent.prompts import EXECUTOR_SYSTEM

    required_phrases = [
        "NEVER fabricate",
        "NEVER claim",
        "INSUFFICIENT DATA",
        "ONLY JSON",
        "No markdown",
    ]
    for phrase in required_phrases:
        assert phrase in EXECUTOR_SYSTEM, (
            f"EXECUTOR_SYSTEM is missing grounding rule: '{phrase}'\n"
            f"Current content:\n{EXECUTOR_SYSTEM}"
        )
