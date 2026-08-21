"""NLI-based factual consistency checker.

Uses an LLM as an NLI (Natural Language Inference) judge to determine
whether a claim is ENTAILED by, CONTRADICTED by, or NEUTRAL to a piece
of evidence. This is the most reliable hallucination check short of
specialised NLI models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

NLIVerdict = Literal["ENTAILS", "CONTRADICTS", "NEUTRAL"]

_NLI_PROMPT = (
    "You are a fact-checking assistant. Given a CLAIM and a PIECE OF EVIDENCE, "
    "determine whether the evidence supports the claim.\n\n"
    "Respond with exactly one word:\n"
    "- ENTAILS   (if the evidence clearly supports the claim)\n"
    "- CONTRADICTS (if the evidence refutes or contradicts the claim)\n"
    "- NEUTRAL   (if the evidence is unrelated or neither supports nor refutes)\n\n"
    "CLAIM: {claim}\n"
    "EVIDENCE: {evidence}\n\n"
    "Verdict:"
)


@dataclass
class NLIResult:
    verdict: NLIVerdict
    confidence: float  # heuristic: 0.9=high, 0.5=uncertain, 0.1=low
    claim: str = ""
    evidence: str = ""


class NLIChecker:
    """LLM-based Natural Language Inference for factual consistency checking."""

    def __init__(self, confidence_map: dict[NLIVerdict, float] | None = None) -> None:
        self._confidence: dict[NLIVerdict, float] = confidence_map or {
            "ENTAILS": 0.9,
            "CONTRADICTS": 0.85,
            "NEUTRAL": 0.5,
        }

    async def check_consistency(
        self,
        claim: str,
        evidence: str,
        provider: LLMProvider,
    ) -> NLIResult:
        """Return an NLI verdict for a single claim–evidence pair.

        Falls back to NEUTRAL on any exception so it never blocks the pipeline.
        """
        try:
            from app.providers.base import CompletionRequest, Message

            prompt = _NLI_PROMPT.format(
                claim=claim.strip()[:400],
                evidence=evidence.strip()[:800],
            )
            req = CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=10,
                temperature=0.0,
            )
            resp = await provider.complete(req)
            raw = (resp.content or "").strip().upper()
            verdict: NLIVerdict = "NEUTRAL"
            for v in ("ENTAILS", "CONTRADICTS", "NEUTRAL"):
                if v in raw:
                    verdict = v  # type: ignore[assignment]
                    break
            return NLIResult(
                verdict=verdict,
                confidence=self._confidence.get(verdict, 0.5),
                claim=claim,
                evidence=evidence,
            )
        except Exception:
            return NLIResult(verdict="NEUTRAL", confidence=0.0, claim=claim, evidence=evidence)

    async def check_answer_consistency(
        self,
        answer: str,
        chunks: list[str],
        provider: LLMProvider,
    ) -> float:
        """Score overall factual consistency of *answer* against *chunks*.

        Returns a float in [0, 1]: 1.0 = fully supported, 0.0 = unsupported.
        """
        if not chunks:
            return 0.5  # no evidence → uncertain
        # Use a single combined evidence string (top 3 chunks to save tokens)
        combined_evidence = " ".join(chunks[:3])[:1200]
        result = await self.check_consistency(
            claim=answer[:500],
            evidence=combined_evidence,
            provider=provider,
        )
        if result.verdict == "ENTAILS":
            return result.confidence
        if result.verdict == "CONTRADICTS":
            return 1.0 - result.confidence
        return 0.5  # NEUTRAL

    async def batch_check(
        self,
        claim_evidence_pairs: list[tuple[str, str]],
        provider: LLMProvider,
    ) -> list[NLIResult]:
        """Check multiple claim–evidence pairs sequentially."""
        results: list[NLIResult] = []
        for claim, evidence in claim_evidence_pairs:
            r = await self.check_consistency(claim, evidence, provider)
            results.append(r)
        return results
