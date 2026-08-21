"""RolePolicy and AgentRole enum — defines which roles are required per PatternConfig."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.pattern_config import PatternConfig


class AgentRole(enum.StrEnum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    CLASSIFIER = "classifier"
    EMBEDDER = "embedder"
    JUDGE = "judge"
    RERANKER = "reranker"


class RolePolicy:
    def get_required_roles(self, config: PatternConfig) -> list[AgentRole]:
        roles = [
            AgentRole.PLANNER,
            AgentRole.EXECUTOR,
            AgentRole.VERIFIER,
            AgentRole.CLASSIFIER,
            AgentRole.EMBEDDER,
        ]
        if any(
            p in ("debate", "consensus", "consensus_verification", "peer_review")
            for p in (config.multi_agent_patterns or [])
        ):
            roles.append(AgentRole.JUDGE)
        roles.append(AgentRole.RERANKER)
        return roles
