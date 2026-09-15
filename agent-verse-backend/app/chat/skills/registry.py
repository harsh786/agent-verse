"""Chat skill registry — the "anything via chat" command surface (Phase 5).

A skill is a thin adapter mapping a named capability to an existing platform
service (goals, triggers, workflows, connectors, knowledge, org-team, governance)
— no new business logic. The registry lets the chat layer discover, describe (for
the planner's tool context), and dispatch skills by name, tenant/permission
filtered by the caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChatSkill:
    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    # JSON-schema-ish arg description for the planner; optional.
    args: dict[str, str] = field(default_factory=dict)
    # Permission scope required to use this skill (checked by the caller).
    scope: str | None = None


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, ChatSkill] = {}

    def register(self, skill: ChatSkill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> ChatSkill | None:
        return self._skills.get(name)

    def list(self) -> list[ChatSkill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    async def dispatch(self, name: str, /, **kwargs: Any) -> Any:
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"unknown skill: {name}")
        return await skill.handler(**kwargs)

    def describe_for_prompt(self, scopes: frozenset[str] | None = None) -> str:
        """Render the skill list for the planner's tool context, filtered to the
        caller's granted *scopes* (None = all)."""
        lines: list[str] = []
        for skill in self.list():
            if scopes is not None and skill.scope is not None and skill.scope not in scopes:
                continue
            arg_str = ", ".join(f"{k}: {v}" for k, v in skill.args.items())
            lines.append(f"- {skill.name}({arg_str}) — {skill.description}")
        return "\n".join(lines)
