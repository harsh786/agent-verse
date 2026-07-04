"""
Claim Grounding Checker
=======================
After each executor step, verifies that concrete claims in the LLM output
actually appear in (or are derivable from) the tool outputs.

Two-pass approach:
1. Deterministic pre-pass: regex extracts entity types (IDs, numbers, URLs,
   dates, quoted strings) and checks each against tool output by substring.
   Fast and zero-cost — handles 80% of cases.

2. LLM grounding check (optional, on request): A cheap model verifies
   that every factual claim is supported by the evidence. Enabled only for
   high-risk steps (write_high/destructive tool calls).

Ungrounded steps:
- Are marked with `UNGROUNDED` in `StepResult.metadata`
- Their summaries include `[UNGROUNDED CLAIM]` markers for the verifier
- Trigger a replan after max 2 consecutive ungrounded steps
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Regex patterns for extractable entity types
_PATTERNS: dict[str, re.Pattern[str]] = {
    "jira_id": re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b"),           # JIRA-123
    "github_pr": re.compile(r"\bPR\s*[#-]?\s*(\d+)\b", re.I),        # PR-42, PR #42, PR42
    "url": re.compile(r"https?://[^\s\"'<>]+"),                     # URLs
    "date": re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),               # ISO dates
    "number": re.compile(r"\b(\d{2,})\b"),                          # Numbers ≥ 10
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "quoted": re.compile(r'"([^"]{4,60})"'),                        # Quoted strings 4-60 chars
}


@dataclass
class GroundingResult:
    grounded: bool
    ungrounded_claims: list[str]
    checked_claims: int
    evidence_length: int


def extract_claims(text: str) -> dict[str, list[str]]:
    """Extract concrete claims from LLM output text."""
    claims: dict[str, list[str]] = {}
    for claim_type, pattern in _PATTERNS.items():
        raw_matches: list[Any] = pattern.findall(text)
        if not raw_matches:
            continue
        # findall returns strings or tuples depending on whether the pattern has groups
        flat: list[str] = []
        for m in raw_matches:
            flat.append(m[0] if isinstance(m, tuple) else m)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in flat:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        claims[claim_type] = unique
    return claims


def check_grounding(
    output: str,
    tool_outputs: list[str],
    *,
    strict: bool = False,
) -> GroundingResult:
    """
    Check if claims in `output` are grounded in `tool_outputs`.

    Args:
        output: The LLM's step output / summary to check.
        tool_outputs: Raw outputs from all tool calls in this step.
        strict: If True, any ungrounded claim → ungrounded result.
                If False (default), 25%+ ungrounded → ungrounded result.

    Returns:
        GroundingResult with grounded status and details.
    """
    if not output or not tool_outputs:
        return GroundingResult(
            grounded=True, ungrounded_claims=[], checked_claims=0, evidence_length=0
        )

    # Combine all tool outputs into evidence string
    evidence = " ".join(str(t) for t in tool_outputs if t)
    evidence_lower = evidence.lower()

    claims = extract_claims(output)
    all_claims: list[str] = []
    for claim_list in claims.values():
        all_claims.extend(claim_list)

    if not all_claims:
        return GroundingResult(
            grounded=True, ungrounded_claims=[], checked_claims=0, evidence_length=len(evidence)
        )

    ungrounded: list[str] = []
    for claim in all_claims:
        # Check if claim appears in evidence (case-insensitive substring)
        if claim.lower() not in evidence_lower:
            ungrounded.append(claim)

    # Threshold: 25% ungrounded → mark as ungrounded
    threshold = 1 if strict else max(1, len(all_claims) // 4)
    grounded = len(ungrounded) < threshold

    if ungrounded:
        logger.info(
            "grounding_check_failed",
            ungrounded_count=len(ungrounded),
            total_claims=len(all_claims),
            ungrounded_samples=ungrounded[:3],
        )

    return GroundingResult(
        grounded=grounded,
        ungrounded_claims=ungrounded,
        checked_claims=len(all_claims),
        evidence_length=len(evidence),
    )


def annotate_ungrounded(step_output: str, result: GroundingResult) -> str:
    """Annotate step output with [UNGROUNDED CLAIM] markers for the verifier."""
    if result.grounded or not result.ungrounded_claims:
        return step_output

    annotated = step_output
    for claim in result.ungrounded_claims[:5]:  # cap at 5 annotations
        annotated = annotated.replace(
            claim,
            f"{claim} [UNGROUNDED CLAIM — not found in tool outputs]",
            1,
        )
    return annotated
