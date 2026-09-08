"""D-4/D-5 wiring: the verifier's final-answer gate must run the composed
NLI claim + attribution check (``verify_grounding``), not only the heuristic
keyword grounding.

``app.intelligence.grounding_verification.verify_grounding`` composes the three
previously-dead components — ``ClaimDecomposer`` (D-4), ``NLIChecker``, and
``AttributionVerifier`` (D-5). It was orphaned (only its own test imported it).
These tests prove (a) the composed pipeline produces the right verdict
deterministically and (b) the verifier module actually references it so the
disconnect cannot silently return.
"""

from __future__ import annotations

import inspect

import pytest

from app.intelligence.grounding_verification import verify_grounding


@pytest.mark.asyncio
async def test_verify_grounding_passes_grounded_answer() -> None:
    verdict = await verify_grounding(
        "The revenue rose by twelve percent.",
        ["Quarterly revenue rose by twelve percent across all regions."],
    )
    assert verdict.safe_to_emit is True
    assert verdict.contradicted_claims == []
    assert verdict.claim_score >= 0.7


@pytest.mark.asyncio
async def test_verify_grounding_flags_fabricated_answer() -> None:
    verdict = await verify_grounding(
        "The CEO is named Zxqwub Fakename and revenue fell 90 percent.",
        ["Quarterly revenue rose by twelve percent across all regions."],
    )
    assert verdict.safe_to_emit is False
    assert verdict.unsupported_claims


def test_verifier_mixin_wires_verify_grounding() -> None:
    """The verifier node must call verify_grounding on the live path (D-4/D-5)."""
    from app.agent.nodes.verifier_mixin import VerifierMixin

    source = inspect.getsource(VerifierMixin._node_verify)
    assert "verify_grounding" in source, (
        "verify_grounding is not called from _node_verify — the NLI claim + "
        "attribution grounding check (D-4/D-5) is disconnected from the live path"
    )
