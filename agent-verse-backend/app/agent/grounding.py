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
    "jira_id": re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b"),  # JIRA-123
    "github_pr": re.compile(r"\bPR\s*[#-]?\s*(\d+)\b", re.I),  # PR-42, PR #42, PR42
    "url": re.compile(r"https?://[^\s\"'<>]+"),  # URLs
    "date": re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),  # ISO dates
    "number": re.compile(r"\b(\d{2,})\b"),  # Numbers ≥ 10
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "quoted": re.compile(r'"([^"]{4,60})"'),  # Quoted strings 4-60 chars
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
    max_ungrounded_ratio: float | None = None,
) -> GroundingResult:
    """
    Check if claims in `output` are grounded in `tool_outputs`.

    Args:
        output: The LLM's step output / summary to check.
        tool_outputs: Raw outputs from all tool calls in this step.
        strict: If True, any ungrounded claim → ungrounded result (ratio 0.0).
        max_ungrounded_ratio: Fraction of claims allowed to be ungrounded.
            When None, defaults to 0.0 for strict and 0.25 otherwise. High/
            critical-risk callers must pass 0.0 for zero tolerance.

    Returns:
        GroundingResult with grounded status and details.

    P0-4: absent evidence with present claims is NOT grounded (was fail-open).
    """
    if not output:
        return GroundingResult(
            grounded=True, ungrounded_claims=[], checked_claims=0, evidence_length=0
        )

    claims = extract_claims(output)
    all_claims: list[str] = []
    for claim_list in claims.values():
        all_claims.extend(claim_list)

    if not all_claims:
        # No concrete claims to verify — nothing can be ungrounded.
        return GroundingResult(
            grounded=True, ungrounded_claims=[], checked_claims=0, evidence_length=0
        )

    if not tool_outputs:
        # Claims exist but there is no evidence at all → cannot be grounded.
        return GroundingResult(
            grounded=False,
            ungrounded_claims=list(all_claims),
            checked_claims=len(all_claims),
            evidence_length=0,
        )

    # Combine all tool outputs into evidence string
    evidence = " ".join(str(t) for t in tool_outputs if t)
    evidence_lower = evidence.lower()

    ungrounded: list[str] = []
    for claim in all_claims:
        # Check if claim appears in evidence (case-insensitive substring)
        if claim.lower() not in evidence_lower:
            ungrounded.append(claim)

    if max_ungrounded_ratio is None:
        max_ungrounded_ratio = 0.0 if strict else 0.25
    allowed = int(len(all_claims) * max_ungrounded_ratio)
    grounded = len(ungrounded) <= allowed

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


# ---------------------------------------------------------------------------
# Phase 3 Track B — new structured grounding interface
# ---------------------------------------------------------------------------


@dataclass
class Claim:
    """A concrete claim extracted from step output."""

    value: str  # e.g. "JIRA-123", "2026-07-15", "5 issues"
    kind: str  # "jira_id" | "date" | "number" | "url" | "quoted" | "email"


def extract_claims_structured(text: str) -> list[Claim]:
    """Extract concrete claims as Claim dataclasses.

    Wraps the existing :func:`extract_claims` dict-returning function and
    converts each (kind, value) pair into a :class:`Claim`.
    """
    claims_dict = extract_claims(text)
    result: list[Claim] = []
    for kind, values in claims_dict.items():
        for v in values:
            result.append(Claim(value=v, kind=kind))
    return result


def deterministic_ground(
    claims: list[Claim],
    tool_output: str,
) -> tuple[list[Claim], list[Claim]]:
    """Check which claims appear in *tool_output* by case-insensitive substring.

    Returns ``(grounded_claims, ungrounded_claims)``.
    """
    evidence_lower = tool_output.lower()
    grounded: list[Claim] = []
    ungrounded: list[Claim] = []
    for claim in claims:
        if claim.value.lower() in evidence_lower:
            grounded.append(claim)
        else:
            ungrounded.append(claim)
    return grounded, ungrounded


class GroundingChecker:
    """Checks whether step output claims are grounded in tool outputs.

    Two-pass: deterministic first, optional LLM second for high-risk steps.
    Fail-closed: on LLM error, residual claims remain UNGROUNDED.
    """

    def __init__(
        self,
        llm_provider: Any | None = None,
        strict: bool = False,
        *,
        provider: Any | None = None,
        model: str = "",
        enabled: bool = True,
    ) -> None:
        # Accept both ``llm_provider`` (positional/keyword) and ``provider`` (keyword)
        self._llm = llm_provider if llm_provider is not None else provider
        self._strict = strict
        self._model = model
        self._enabled = enabled

    def _sync_check(
        self,
        step_output: str,
        tool_outputs: list[str],
        *,
        high_risk: bool = False,
    ) -> GroundingResult:
        """Deterministic synchronous check. Delegates to :func:`check_grounding`."""
        return check_grounding(
            step_output,
            tool_outputs,
            strict=self._strict or high_risk,
        )

    async def check(
        self,
        step_output: str = "",
        tool_outputs: list[str] | None = None,
        *,
        high_risk: bool = False,
        # Alternate keyword spellings (plan Task 6 calling convention)
        answer: str | None = None,
        tool_output: str | None = None,
        tenant_id: str = "",
    ) -> GroundingResult:
        """Unified async grounding check.

        Supports both positional (``step_output``, ``tool_outputs``) and
        keyword (``answer``, ``tool_output``) calling conventions.

        Pass 1: deterministic substring check (always runs).
        Pass 2: optional LLM check when provider is set and residuals remain
        (fail-closed — errors leave claims UNGROUNDED).
        """
        # Normalise arguments
        _output = answer if answer is not None else step_output
        _outputs: list[str]
        if tool_output is not None:
            _outputs = [tool_output]
        elif tool_outputs is not None:
            _outputs = tool_outputs
        else:
            _outputs = []

        result = self._sync_check(_output, _outputs, high_risk=high_risk)

        # Fast path: everything grounded or no claims to check
        if result.grounded or result.checked_claims == 0:
            return result

        # LLM pass for residual ungrounded claims (fail-closed)
        if self._llm is not None:
            try:
                import json as _json

                from app.agent.prompts import GROUNDING_SYSTEM
                from app.providers.base import CompletionRequest, Message

                evidence = " ".join(str(t) for t in _outputs[:3])[:2000]
                req = CompletionRequest(
                    messages=[
                        Message(role="system", content=GROUNDING_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Output to check:\n{_output[:500]}\n\n"
                                f"Tool evidence:\n{evidence}\n\n"
                                f"Residual ungrounded claims: {result.ungrounded_claims[:5]}\n\n"
                                "For each claim, state if it is grounded or ungrounded. "
                                "Return ONLY JSON: "
                                '{"grounded": ["claim1"], "ungrounded": ["claim2"]}'
                            ),
                        ),
                    ],
                    model=self._model,
                )
                resp = await self._llm.complete(req)
                data = _json.loads(resp.content)
                still_ungrounded: list[str] = data.get("ungrounded", result.ungrounded_claims)
                return GroundingResult(
                    grounded=len(still_ungrounded) == 0,
                    ungrounded_claims=still_ungrounded,
                    checked_claims=result.checked_claims,
                    evidence_length=result.evidence_length,
                )
            except Exception as exc:
                logger.debug("grounding_llm_check_failed", error=str(exc)[:60])
            # Fail-closed: residual claims stay ungrounded

        return result

    async def check_async(
        self,
        step_output: str,
        tool_outputs: list[str],
        *,
        high_risk: bool = False,
    ) -> GroundingResult:
        """Alias for :meth:`check` using the positional-argument calling convention."""
        return await self.check(step_output, tool_outputs, high_risk=high_risk)
