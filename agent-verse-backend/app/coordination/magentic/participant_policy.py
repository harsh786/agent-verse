"""Deterministic policy-aware Magentic participant selection."""

from __future__ import annotations

from app.coordination.magentic.models import ParticipantCandidate, ParticipantDecision


def select_participant(
    candidates: tuple[ParticipantCandidate, ...],
    *,
    required_capabilities: frozenset[str],
    remaining_deadline_ms: int,
) -> ParticipantDecision:
    eligible: list[ParticipantCandidate] = []
    rejected: list[tuple[str, str]] = []
    for candidate in candidates:
        reason = ""
        if not candidate.available:
            reason = "unavailable"
        elif not candidate.policy_eligible:
            reason = "policy_denied"
        elif not required_capabilities <= candidate.capabilities:
            reason = "capability_mismatch"
        elif candidate.estimated_latency_ms > remaining_deadline_ms:
            reason = "deadline_infeasible"
        if reason:
            rejected.append((candidate.agent_id, reason))
        else:
            eligible.append(candidate)
    if not eligible:
        raise RuntimeError("no eligible Magentic participant")
    selected = min(
        eligible,
        key=lambda item: (
            item.recent_no_progress_assignments,
            item.current_load,
            item.estimated_latency_ms,
            item.agent_id,
        ),
    )
    rejected.extend(
        (item.agent_id, "lower_score") for item in eligible if item.agent_id != selected.agent_id
    )
    return ParticipantDecision(
        selected_agent_id=selected.agent_id,
        rejected=tuple(sorted(rejected)),
    )


__all__ = ["select_participant"]
