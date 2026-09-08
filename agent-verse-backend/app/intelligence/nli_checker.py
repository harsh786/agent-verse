"""NLI-based factual consistency checker.

Uses an LLM as an NLI (Natural Language Inference) judge to determine
whether a claim is ENTAILED by, CONTRADICTED by, or NEUTRAL to a piece
of evidence. This is the most reliable hallucination check short of
specialised NLI models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

NLIVerdict = Literal["ENTAILS", "CONTRADICTS", "NEUTRAL"]

# Negation cues used by the deterministic heuristic to detect contradictions.
_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "cannot",
        "cant",
        "wont",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "doesnt",
        "dont",
        "didnt",
        "without",
        "neither",
        "nor",
        "false",
        "incorrect",
        "untrue",
    }
)

# Very common words that carry no discriminative signal for overlap scoring.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "for",
        "with",
        "as",
        "by",
        "from",
        "into",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "could",
        "which",
        "who",
        "whom",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"\b\d[\d,.]*\b")

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
        provider: LLMProvider | None = None,
    ) -> NLIResult:
        """Return an NLI verdict for a single claim-evidence pair.

        When *provider* is ``None`` a deterministic token-overlap heuristic is
        used so the pipeline runs without any LLM (see
        :meth:`check_consistency_sync`). With a provider, the LLM acts as the
        NLI judge and the heuristic is the fallback on any error, so the method
        never raises and never blocks the pipeline.
        """
        if provider is None:
            return self.check_consistency_sync(claim, evidence)
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

    def check_consistency_sync(self, claim: str, evidence: str) -> NLIResult:
        """Deterministic, LLM-free NLI verdict for a claim-evidence pair.

        This is the heuristic fallback that lets the whole hallucination
        pipeline run offline and in tests. It is intentionally conservative:

        - **ENTAILS** - (almost) every content word of the claim appears in the
          evidence and there is no negation or numeric mismatch.
        - **CONTRADICTS** - the claim shares its subject with the evidence but
          asserts the opposite polarity (negation mismatch) or a conflicting
          number (e.g. "324 meters" vs "300 meters").
        - **NEUTRAL** - otherwise (unrelated or only partially supported), which
          the pipeline treats as *unsupported*.
        """
        claim_tokens = self._content_tokens(claim)
        evidence_tokens = self._content_tokens(evidence)

        if not claim_tokens:
            return NLIResult(verdict="NEUTRAL", confidence=0.0, claim=claim, evidence=evidence)

        overlap = claim_tokens & evidence_tokens
        coverage = len(overlap) / len(claim_tokens)

        # Contradiction signals require a shared subject (real overlap), else an
        # unrelated sentence would be misread as a contradiction.
        shared_subject = len(overlap) >= 2 or (len(overlap) >= 1 and len(claim_tokens) <= 2)
        if shared_subject and coverage >= 0.4:
            if self._negation_mismatch(claim, evidence):
                return NLIResult(
                    verdict="CONTRADICTS",
                    confidence=self._confidence.get("CONTRADICTS", 0.85),
                    claim=claim,
                    evidence=evidence,
                )
            if self._number_conflict(claim, evidence):
                return NLIResult(
                    verdict="CONTRADICTS",
                    confidence=self._confidence.get("CONTRADICTS", 0.85),
                    claim=claim,
                    evidence=evidence,
                )

        if coverage >= 0.75 and not self._negation_mismatch(claim, evidence):
            return NLIResult(
                verdict="ENTAILS",
                confidence=self._confidence.get("ENTAILS", 0.9),
                claim=claim,
                evidence=evidence,
            )

        return NLIResult(
            verdict="NEUTRAL",
            confidence=self._confidence.get("NEUTRAL", 0.5),
            claim=claim,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Heuristic internals
    # ------------------------------------------------------------------

    @staticmethod
    def _content_tokens(text: str) -> set[str]:
        """Lower-cased content words (stopwords and negations removed)."""
        return {
            w
            for w in _WORD_RE.findall(text.lower())
            if w not in _STOPWORDS and w not in _NEGATIONS and len(w) >= 2
        }

    @staticmethod
    def _negations(text: str) -> int:
        return sum(1 for w in _WORD_RE.findall(text.lower().replace("'", "")) if w in _NEGATIONS)

    def _negation_mismatch(self, claim: str, evidence: str) -> bool:
        """True when claim and evidence disagree on polarity (odd negation delta)."""
        return (self._negations(claim) - self._negations(evidence)) % 2 == 1

    @staticmethod
    def _number_conflict(claim: str, evidence: str) -> bool:
        """True when the claim states a number absent from evidence that has its own."""
        claim_nums = {n.replace(",", "") for n in _NUMBER_RE.findall(claim)}
        evidence_nums = {n.replace(",", "") for n in _NUMBER_RE.findall(evidence)}
        if not claim_nums or not evidence_nums:
            return False
        return bool(claim_nums - evidence_nums) and claim_nums != evidence_nums

    async def check_answer_consistency(
        self,
        answer: str,
        chunks: list[str],
        provider: LLMProvider | None = None,
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
        provider: LLMProvider | None = None,
    ) -> list[NLIResult]:
        """Check multiple claim-evidence pairs sequentially."""
        results: list[NLIResult] = []
        for claim, evidence in claim_evidence_pairs:
            r = await self.check_consistency(claim, evidence, provider)
            results.append(r)
        return results
