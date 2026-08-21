"""Claim Decomposer — break an answer into atomic claims and verify each.

For rigorous hallucination detection, rather than checking the whole answer
at once, we decompose it into small atomic factual claims and verify each
claim independently against the retrieved evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.intelligence.nli_checker import NLIChecker, NLIVerdict
    from app.providers.base import LLMProvider

_DECOMPOSE_PROMPT = (
    "Break the following answer into simple, atomic, standalone factual claims. "
    "Each claim must be a single declarative sentence. "
    "Output only the claims, one per line, no numbering.\n\n"
    "Answer: {answer}\n\nClaims:"
)


@dataclass
class ClaimVerificationReport:
    claims: list[str]
    verdicts: list[NLIVerdict]
    overall_score: float  # fraction of claims that are ENTAILED
    unsupported_claims: list[str]
    contradicted_claims: list[str]
    evidence_chunks: list[str] = field(default_factory=list)


class ClaimDecomposer:
    """Decompose text into atomic claims and verify each against evidence."""

    async def decompose(
        self,
        text: str,
        provider: LLMProvider,
    ) -> list[str]:
        """Split *text* into atomic factual claims via LLM.

        Falls back to sentence splitting on failure.
        """
        try:
            from app.providers.base import CompletionRequest, Message

            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=_DECOMPOSE_PROMPT.format(answer=text[:800]),
                    )
                ],
                model="",
                max_tokens=300,
                temperature=0.0,
            )
            resp = await provider.complete(req)
            raw = (resp.content or "").strip()
            claims = [c.strip() for c in raw.splitlines() if c.strip() and len(c.strip()) > 10]
            return claims[:20]  # cap to avoid token explosion
        except Exception:
            return self._sentence_split(text)

    def decompose_sync(self, text: str) -> list[str]:
        """Sentence-split fallback for use without an LLM."""
        return self._sentence_split(text)

    async def verify_claims(
        self,
        claims: list[str],
        evidence_chunks: list[str],
        nli: NLIChecker,
        provider: LLMProvider,
    ) -> ClaimVerificationReport:
        """Verify each claim against the combined evidence.

        Parameters
        ----------
        claims : list[str]
            Atomic claims to verify.
        evidence_chunks : list[str]
            Retrieved RAG chunks that constitute the ground truth.
        nli : NLIChecker
            NLI checking engine.
        provider : LLMProvider
            LLM provider for NLI calls.
        """
        combined_evidence = " ".join(evidence_chunks[:3])[:1500]
        verdicts: list[NLIVerdict] = []
        unsupported: list[str] = []
        contradicted: list[str] = []

        for claim in claims:
            result = await nli.check_consistency(claim, combined_evidence, provider)
            verdicts.append(result.verdict)
            if result.verdict == "NEUTRAL":
                unsupported.append(claim)
            elif result.verdict == "CONTRADICTS":
                contradicted.append(claim)

        entailed_count = sum(1 for v in verdicts if v == "ENTAILS")
        overall = entailed_count / max(len(claims), 1)

        return ClaimVerificationReport(
            claims=claims,
            verdicts=verdicts,
            overall_score=round(overall, 3),
            unsupported_claims=unsupported,
            contradicted_claims=contradicted,
            evidence_chunks=evidence_chunks,
        )

    async def check_answer(
        self,
        answer: str,
        evidence_chunks: list[str],
        nli: NLIChecker,
        provider: LLMProvider,
    ) -> ClaimVerificationReport:
        """Convenience: decompose + verify in one call."""
        claims = await self.decompose(answer, provider)
        return await self.verify_claims(claims, evidence_chunks, nli, provider)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sentence_split(self, text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10][:15]
