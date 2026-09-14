"""Citation gate for synthesized answers (Hallucination T4).

``SYNTHESIS_SYSTEM`` instructs the model to cite every factual claim as
``[Step N]``. This gate ENFORCES it: for each sentence carrying a typed claim
(id/number/date/url/email/quoted), there must be a ``[Step N]`` reference whose
cited step output actually contains the claim (verified with the typed,
normalized matcher). Sentences that assert a claim with no citation, or cite a
step that does not support it, are violations — stripped (default) or, for a
zero-tolerance caller, cause rejection. Pure prose (no typed claims) is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.grounding import claim_grounded_in, extract_claims

_STEP_REF = re.compile(r"\[step\s*(\d+)\]", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class CitationResult:
    ok: bool
    gated_answer: str
    violations: list[str] = field(default_factory=list)
    kept_sentences: int = 0
    dropped_sentences: int = 0


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def enforce_citations(
    answer: str,
    step_outputs: dict[int, str],
    *,
    strip: bool = True,
) -> CitationResult:
    """Validate/gate ``[Step N]`` citations against the cited step outputs.

    ``step_outputs`` maps the 1-based step number used in ``[Step N]`` to that
    step's output text.
    """
    if not answer.strip():
        return CitationResult(ok=True, gated_answer="")

    lowered = {n: (out or "").lower() for n, out in step_outputs.items()}
    kept: list[str] = []
    violations: list[str] = []
    dropped = 0

    for sentence in _sentences(answer):
        typed = [(v, k) for k, vs in extract_claims(sentence).items() for v in vs]
        if not typed:
            kept.append(sentence)  # prose without a concrete claim
            continue

        cited_steps = [int(n) for n in _STEP_REF.findall(sentence)]
        sentence_ok = True
        if not cited_steps:
            violations.append(f"uncited claim(s) {[v for v, _ in typed]}: {sentence[:80]}")
            sentence_ok = False
        else:
            cited_evidence = " ".join(lowered.get(n, "") for n in cited_steps)
            for value, kind in typed:
                if not claim_grounded_in(value, kind, cited_evidence):
                    violations.append(
                        f"claim '{value}' not supported by cited {cited_steps}: {sentence[:80]}"
                    )
                    sentence_ok = False

        if sentence_ok:
            kept.append(sentence)
        else:
            dropped += 1

    gated = " ".join(kept) if strip else answer
    return CitationResult(
        ok=not violations,
        gated_answer=gated,
        violations=violations,
        kept_sentences=len(kept),
        dropped_sentences=dropped,
    )
