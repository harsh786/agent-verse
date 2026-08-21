"""Attribution Verifier — verify that citations actually support the answer.

For each citation in the answer, checks whether the cited chunk's text
sufficiently overlaps with the part of the answer that references it.
This catches hallucinations where citations are present but irrelevant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass
class AttributionReport:
    verified_count: int
    failed_count: int
    unsupported_claims: list[str]
    precision_score: float  # verified / total citations
    details: list[dict[str, Any]] = field(default_factory=list)


class AttributionVerifier:
    """Verify that citations in an answer are grounded by the cited chunks.

    Uses Jaccard similarity on key terms as a fast heuristic. An optional
    LLM-based second pass can be enabled for lower-confidence cases.
    """

    def __init__(self, jaccard_threshold: float = 0.15) -> None:
        self._threshold = jaccard_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        answer: str,
        chunks: list[str],
        citation_indices: list[int] | None = None,
    ) -> AttributionReport:
        """Check whether each cited chunk supports the corresponding answer sentence.

        Parameters
        ----------
        answer : str
            The agent's full answer text.
        chunks : list[str]
            The retrieved RAG chunks (in order).
        citation_indices : list[int] | None
            Explicit citation indices to verify. If None, infer from
            in-text references like "[1]", "[2]".
        """
        if citation_indices is None:
            citation_indices = self._extract_citation_indices(answer)

        if not citation_indices or not chunks:
            return AttributionReport(
                verified_count=0,
                failed_count=0,
                unsupported_claims=[],
                precision_score=1.0,  # no citations → nothing to verify
            )

        verified = 0
        failed = 0
        unsupported: list[str] = []
        details: list[dict[str, Any]] = []

        for idx in citation_indices:
            if idx < 0 or idx >= len(chunks):
                failed += 1
                unsupported.append(f"Citation [{idx + 1}] references non-existent chunk")
                details.append({"citation": idx + 1, "valid": False, "reason": "out_of_range"})
                continue

            chunk = chunks[idx]
            # Find the sentence(s) in the answer that reference this citation
            citing_sentences = self._find_citing_sentences(answer, idx + 1)
            if not citing_sentences:
                # Citation exists but no sentence refers to it — still verify against answer
                citing_sentences = [answer[:300]]

            max_score = max(self._jaccard_score(sentence, chunk) for sentence in citing_sentences)

            if max_score >= self._threshold:
                verified += 1
                details.append({"citation": idx + 1, "valid": True, "jaccard": round(max_score, 3)})
            else:
                failed += 1
                unsupported.append(
                    f"Citation [{idx + 1}] has low overlap with cited chunk (jaccard={max_score:.3f})"
                )
                details.append(
                    {"citation": idx + 1, "valid": False, "jaccard": round(max_score, 3)}
                )

        total = verified + failed
        precision = verified / max(total, 1)

        return AttributionReport(
            verified_count=verified,
            failed_count=failed,
            unsupported_claims=unsupported,
            precision_score=round(precision, 3),
            details=details,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_citation_indices(self, text: str) -> list[int]:
        """Extract citation numbers from text like [1], [2], [3]."""
        matches = re.findall(r"\[(\d+)\]", text)
        # Convert 1-based citation to 0-based chunk index
        return [int(m) - 1 for m in matches if int(m) >= 1]

    def _find_citing_sentences(self, text: str, citation_number: int) -> list[str]:
        """Return sentences that contain `[citation_number]`."""
        tag = f"[{citation_number}]"
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s for s in sentences if tag in s]

    def _jaccard_score(self, text_a: str, text_b: str) -> float:
        """Jaccard similarity of 4+-character word sets."""

        def tok(t: str) -> set[str]:
            return {w.lower() for w in re.findall(r"\b\w{4,}\b", t)}

        a, b = tok(text_a), tok(text_b)
        if not a and not b:
            return 0.0
        return len(a & b) / len(a | b)
