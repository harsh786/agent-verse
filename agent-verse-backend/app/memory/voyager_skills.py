"""Governed versioned Voyager skill publication."""

from __future__ import annotations

from app.memory.procedural_validator import ProcedureContract, validate_procedure


class VoyagerSkillStore:
    def __init__(self) -> None:
        self._skills: dict[tuple[str, str, str], ProcedureContract] = {}

    def publish(
        self,
        skill: ProcedureContract,
        *,
        available_tools: dict[str, str],
        allowed_capabilities: frozenset[str],
        ready_connectors: frozenset[str],
        policy_fingerprint: str,
    ) -> ProcedureContract:
        validate_procedure(
            skill,
            tenant_id=skill.tenant_id,
            available_tools=available_tools,
            allowed_capabilities=allowed_capabilities,
            ready_connectors=ready_connectors,
            policy_fingerprint=policy_fingerprint,
        )
        key = (skill.tenant_id, skill.procedure_id, skill.skill_version)
        prior = self._skills.get(key)
        if prior is not None and prior != skill:
            raise ValueError("skill version is immutable")
        self._skills[key] = skill
        return prior or skill


__all__ = ["VoyagerSkillStore"]
