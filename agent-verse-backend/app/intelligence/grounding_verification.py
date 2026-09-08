"""Grounding verification — the reusable hallucination-handling entrypoint.

This module composes the three previously-dead components into one call:

- :class:`~app.intelligence.claim_decomposer.ClaimDecomposer` — split an answer
  into atomic factual claims.
- :class:`~app.intelligence.nli_checker.NLIChecker` — classify each claim
  against the retrieved evidence as ENTAILS / CONTRADICTS / NEUTRAL.
- :class:`~app.evals.attribution_verifier.AttributionVerifier` — check that each
  in-text citation (``[1]``) is actually supported by the chunk it cites.

The single public function :func:`verify_grounding` returns a
:class:`GroundingVerdict` — supported / unsupported / contradicted claims, a
claim-support score, an attribution (citation-precision) score, and a
``safe_to_emit`` boolean.

It works fully deterministically when ``provider`` is ``None`` (heuristic NLI +
sentence-split decomposition), so it is safe to call in hot paths and tests
without any LLM.

TODO(verifier-loop wiring): invoke :func:`verify_grounding` from
``app/agent/nodes/verifier_mixin.py`` (the ``VerifierMixin.verify`` /
``_run_verifier`` path, after tool outputs are gathered into the step evidence)
so a low ``safe_to_emit`` verdict forces a replan instead of emitting an
ungrounded / contradicted answer. Wiring is deliberately out of scope here; this
module is the callable that wiring will use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.evals.attribution_verifier import AttributionReport, AttributionVerifier
from app.intelligence.claim_decomposer import ClaimDecomposer, ClaimVerificationReport
from app.intelligence.nli_checker import NLIChecker

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

# Defaults chosen so a single unsupported/contradicted atomic claim in a short
# answer flips ``safe_to_emit`` to False, while fully-grounded answers pass.
_DEFAULT_CLAIM_THRESHOLD = 0.7
_DEFAULT_ATTRIBUTION_THRESHOLD = 0.5


@dataclass
class GroundingVerdict:
    """Structured hallucination verdict for an answer against its evidence."""

    safe_to_emit: bool
    supported_claims: list[str]
    unsupported_claims: list[str]
    contradicted_claims: list[str]
    claim_score: float  # fraction of atomic claims entailed by evidence [0, 1]
    attribution_score: float  # fraction of citations backed by the cited chunk [0, 1]
    reasons: list[str] = field(default_factory=list)
    claim_report: ClaimVerificationReport | None = None
    attribution_report: AttributionReport | None = None


async def verify_grounding(
    answer: str,
    evidence_chunks: list[str],
    citations: list[int] | None = None,
    *,
    provider: LLMProvider | None = None,
    decomposer: ClaimDecomposer | None = None,
    nli: NLIChecker | None = None,
    attribution: AttributionVerifier | None = None,
    claim_threshold: float = _DEFAULT_CLAIM_THRESHOLD,
    attribution_threshold: float = _DEFAULT_ATTRIBUTION_THRESHOLD,
) -> GroundingVerdict:
    """Verify that *answer* is grounded in *evidence_chunks*.

    Parameters
    ----------
    answer:
        The generated answer text to check.
    evidence_chunks:
        Retrieved source chunks that constitute the ground truth, in citation
        order (``[1]`` → ``evidence_chunks[0]``).
    citations:
        Explicit 0-based chunk indices cited by the answer. When ``None`` they
        are inferred from in-text ``[n]`` markers by the attribution verifier.
    provider:
        Optional LLM provider. When ``None``, decomposition falls back to
        sentence splitting and NLI to its deterministic heuristic, so the whole
        check runs without an LLM.
    claim_threshold / attribution_threshold:
        Minimum claim-support and attribution scores required for
        ``safe_to_emit``.

    Returns
    -------
    GroundingVerdict
        ``safe_to_emit`` is True only when there are **no contradicted claims**,
        the claim-support score is at least ``claim_threshold``, and the
        attribution score is at least ``attribution_threshold``.
    """
    decomposer = decomposer or ClaimDecomposer()
    nli = nli or NLIChecker()
    attribution = attribution or AttributionVerifier()

    # 1. Decompose + NLI-verify atomic claims against the evidence.
    claim_report = await decomposer.check_answer(
        answer=answer,
        evidence_chunks=evidence_chunks,
        nli=nli,
        provider=provider,
    )

    # 2. Verify citations resolve to chunks that actually support them.
    attribution_report = attribution.verify(answer, evidence_chunks, citations)

    supported = [
        claim
        for claim, verdict in zip(claim_report.claims, claim_report.verdicts, strict=False)
        if verdict == "ENTAILS"
    ]

    reasons: list[str] = []
    if claim_report.contradicted_claims:
        reasons.append(f"{len(claim_report.contradicted_claims)} contradicted claim(s)")
    if claim_report.unsupported_claims:
        reasons.append(f"{len(claim_report.unsupported_claims)} unsupported claim(s)")
    if claim_report.overall_score < claim_threshold:
        reasons.append(
            f"claim support {claim_report.overall_score:.2f} < threshold {claim_threshold:.2f}"
        )
    if attribution_report.precision_score < attribution_threshold:
        reasons.append(
            f"attribution {attribution_report.precision_score:.2f} "
            f"< threshold {attribution_threshold:.2f}"
        )

    safe_to_emit = (
        not claim_report.contradicted_claims
        and claim_report.overall_score >= claim_threshold
        and attribution_report.precision_score >= attribution_threshold
    )

    return GroundingVerdict(
        safe_to_emit=safe_to_emit,
        supported_claims=supported,
        unsupported_claims=list(claim_report.unsupported_claims),
        contradicted_claims=list(claim_report.contradicted_claims),
        claim_score=claim_report.overall_score,
        attribution_score=attribution_report.precision_score,
        reasons=reasons,
        claim_report=claim_report,
        attribution_report=attribution_report,
    )
