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
    context_evidence: list[str] | None = None,
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
    context_evidence:
        Extra evidence claims may be entailed by but that no citation refers to
        (e.g. recomputed arithmetic). It is checked FIRST by the claim verifier
        (which reads a bounded prefix of the evidence) and never shifts the
        ``[n]`` -> ``evidence_chunks[n-1]`` citation mapping.

    An answer with no atomic claim at all (a fragment such as "ACK", "Done" or
    "391" — the deterministic decomposer drops sentences of 10 characters or
    less) asserts nothing the claim check can support: it is vacuously supported
    (claim score 1.0) unless the NLI checker finds the whole answer contradicted
    by the evidence. It used to score 0/1 = 0.0 and failed every such answer
    (B7 live open item 1); its concrete tokens are still checked by the keyword
    grounding gate.

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
    claim_evidence = [*(context_evidence or []), *evidence_chunks]
    claim_report = await decomposer.check_answer(
        answer=answer,
        evidence_chunks=claim_evidence,
        nli=nli,
        provider=provider,
    )
    if not claim_report.claims and answer.strip():
        claim_report = await _fragment_report(answer.strip(), claim_evidence, nli, provider)

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


async def _fragment_report(
    answer: str,
    evidence_chunks: list[str],
    nli: NLIChecker,
    provider: LLMProvider | None,
) -> ClaimVerificationReport:
    """Claim report for an answer that holds no atomic claim (see verify_grounding).

    Vacuously supported (score 1.0), unless the whole fragment is CONTRADICTED by
    the evidence ("Done" after a failed delete), which is reported as one
    contradicted claim.
    """
    combined = " ".join(evidence_chunks[:3])[:1500]
    verdict = (await nli.check_consistency(answer, combined, provider)).verdict
    contradicted = verdict == "CONTRADICTS"
    return ClaimVerificationReport(
        claims=[answer] if contradicted else [],
        verdicts=[verdict] if contradicted else [],
        overall_score=0.0 if contradicted else 1.0,
        unsupported_claims=[],
        contradicted_claims=[answer] if contradicted else [],
        evidence_chunks=evidence_chunks,
    )
