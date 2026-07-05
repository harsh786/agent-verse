"""
Agent Delegation Lineage
=========================
When agent A spawns agent B (e.g., in civilization or supervisor mode),
the full delegation chain is recorded in every audit entry and tool call.

This makes the authorization chain fully traceable:
"User Alice → Agent CEO → Agent CTO → Agent Dev → called github_create_pr"

Lineage is stored in:
- agent_state.context["delegation_chain"]
- Every audit record
- Every tool call log
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DelegationLink:
    """One link in the delegation chain."""

    actor_type: str  # "user" | "agent" | "system"
    actor_id: str
    actor_name: str
    action: str  # "spawned" | "delegated_goal" | "submitted"
    timestamp: float
    scope_constraints: list[str] = field(default_factory=list)  # what this agent can do


@dataclass
class DelegationChain:
    """The full chain from root user/system to the current executing agent."""

    links: list[DelegationLink] = field(default_factory=list)
    root_user_id: str = ""
    root_tenant_id: str = ""

    def push(self, link: DelegationLink) -> None:
        self.links.append(link)

    def depth(self) -> int:
        return len(self.links)

    def current_actor(self) -> DelegationLink | None:
        return self.links[-1] if self.links else None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth(),
            "root_user_id": self.root_user_id,
            "root_tenant_id": self.root_tenant_id,
            "chain": [
                {
                    "type": link.actor_type,
                    "id": link.actor_id,
                    "name": link.actor_name,
                    "action": link.action,
                }
                for link in self.links
            ],
        }

    def to_string(self) -> str:
        """Human-readable: User:alice → Agent:CEO → Agent:Dev"""
        parts = [f"{link.actor_type}:{link.actor_name}" for link in self.links]
        return " → ".join(parts)

    @classmethod
    def for_direct_goal(
        cls,
        *,
        user_id: str,
        tenant_id: str,
        agent_id: str,
        agent_name: str,
        goal: str,
    ) -> DelegationChain:
        chain = cls(root_user_id=user_id, root_tenant_id=tenant_id)
        if user_id:
            chain.push(
                DelegationLink(
                    actor_type="user",
                    actor_id=user_id,
                    actor_name=user_id,
                    action="submitted",
                    timestamp=time.time(),
                )
            )
        chain.push(
            DelegationLink(
                actor_type="agent",
                actor_id=agent_id,
                actor_name=agent_name,
                action="executing",
                timestamp=time.time(),
            )
        )
        return chain

    def extend_for_spawn(
        self,
        *,
        child_agent_id: str,
        child_agent_name: str,
        scope_constraints: list[str] | None = None,
    ) -> DelegationChain:
        """Create a new chain for a spawned sub-agent."""
        child = copy.deepcopy(self)
        child.push(
            DelegationLink(
                actor_type="agent",
                actor_id=child_agent_id,
                actor_name=child_agent_name,
                action="spawned",
                timestamp=time.time(),
                scope_constraints=scope_constraints or [],
            )
        )
        return child
