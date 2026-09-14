"""Per-claim evidence scoring + calibrated abstention (Hallucination T2/T3).

Layers a policy on top of the typed grounding tiers in ``grounding.py``:

1. exact tier — ``claim_grounded_in`` (normalized token/date/substring match);
2. embedding tier — optional; a paraphrased *name/quoted* claim can still ground
   against an evidence span above a cosine threshold (numbers/ids/dates never use
   this — they must match exactly, so "50" can't fuzzy-match "5");
3. abstention — claims that ground on no tier are returned as ``abstain`` so the
   executor emits ``INSUFFICIENT DATA`` for them instead of asserting the value.

``GroundingPolicy`` yields per-claim verdicts and a grounded ratio judged against
a configurable threshold (per plan/tenant), so callers get calibrated
"grounded / abstain" decisions rather than a single boolean.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.agent.grounding import claim_grounded_in, extract_claims

# Kinds that MUST match exactly — fuzzy/embedding matching a number or id is unsafe.
_EXACT_ONLY_KINDS = frozenset({"number", "github_pr", "date", "jira_id", "url", "email"})

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True)
class ClaimVerdict:
    value: str
    kind: str
    grounded: bool
    tier: str  # "exact" | "embedding" | "none"
    score: float = 0.0  # embedding similarity when tier == "embedding"


@dataclass
class PolicyResult:
    grounded: bool
    grounded_ratio: float
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    abstain: list[str] = field(default_factory=list)  # claims to replace with INSUFFICIENT DATA


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _evidence_spans(evidence: str, max_spans: int = 40) -> list[str]:
    spans = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", evidence) if s.strip()]
    return spans[:max_spans]


class GroundingPolicy:
    def __init__(
        self,
        *,
        min_grounded_ratio: float = 0.75,
        numeric_claims_require_exact: bool = True,
        embed_fn: EmbedFn | None = None,
        embed_threshold: float = 0.82,
    ) -> None:
        self._min_ratio = min_grounded_ratio
        self._numeric_exact = numeric_claims_require_exact
        self._embed_fn = embed_fn
        self._embed_threshold = embed_threshold

    async def evaluate(self, output: str, tool_outputs: list[str]) -> PolicyResult:
        typed = [(v, k) for k, vs in extract_claims(output).items() for v in vs]
        if not typed:
            return PolicyResult(grounded=True, grounded_ratio=1.0)

        evidence = " ".join(str(t) for t in tool_outputs if t)
        evidence_lower = evidence.lower()

        verdicts: list[ClaimVerdict] = []
        # Exact tier first.
        residual: list[tuple[str, str]] = []
        for value, kind in typed:
            if evidence and claim_grounded_in(value, kind, evidence_lower):
                verdicts.append(ClaimVerdict(value, kind, True, "exact"))
            else:
                residual.append((value, kind))

        # Embedding tier for paraphrase-eligible residual claims.
        embeddable = [
            (v, k)
            for v, k in residual
            if self._embed_fn is not None
            and not (self._numeric_exact and k in _EXACT_ONLY_KINDS)
        ]
        embed_hits: dict[str, tuple[bool, float]] = {}
        if embeddable and evidence:
            spans = _evidence_spans(evidence)
            if spans:
                try:
                    vecs = await self._embed_fn([v for v, _ in embeddable] + spans)
                    n = len(embeddable)
                    claim_vecs, span_vecs = vecs[:n], vecs[n:]
                    for (value, _kind), cvec in zip(embeddable, claim_vecs, strict=False):
                        best = max((_cosine(cvec, s) for s in span_vecs), default=0.0)
                        embed_hits[value] = (best >= self._embed_threshold, best)
                except Exception:
                    embed_hits = {}  # embedding tier best-effort

        for value, kind in residual:
            if value in embed_hits:
                ok, score = embed_hits[value]
                verdicts.append(ClaimVerdict(value, kind, ok, "embedding" if ok else "none", score))
            else:
                verdicts.append(ClaimVerdict(value, kind, False, "none"))

        grounded_n = sum(1 for v in verdicts if v.grounded)
        ratio = grounded_n / len(verdicts)
        abstain = [v.value for v in verdicts if not v.grounded]
        return PolicyResult(
            grounded=ratio >= self._min_ratio,
            grounded_ratio=round(ratio, 4),
            verdicts=verdicts,
            abstain=abstain,
        )
