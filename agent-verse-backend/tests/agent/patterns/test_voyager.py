from __future__ import annotations

import pytest

from app.agent.patterns.voyager import VoyagerRuntime
from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.memory.voyager_skills import VoyagerSkillStore


@pytest.mark.asyncio
async def test_voyager_is_bounded_evidence_only_and_restart_safe() -> None:
    calls: list[str] = []
    runtime = VoyagerRuntime(
        checkpoint_store=InMemoryPatternCheckpointStore(), skill_store=VoyagerSkillStore()
    )

    async def run_task(gap):
        calls.append(gap)
        return {"evidence_ref": f"evidence://{gap}"}

    kwargs = {
        "session_id": "session",
        "execution_id": "execution",
        "tenant_id": "tenant",
        "capability_gaps": ("search", "search", "summarize"),
        "run_task": run_task,
        "synthesize_skill": lambda *_: {
            "procedure_id": "skill",
            "skill_version": "v1",
            "tool_sequence": ("search",),
            "required_capabilities": frozenset({"research"}),
            "tool_schema_versions": {"search": "v2"},
            "connector_ids": frozenset({"docs"}),
            "policy_fingerprint": "policy-v1",
        },
        "maximum_tasks": 2,
        "publication_context": {
            "available_tools": {"search": "v2"},
            "allowed_capabilities": frozenset({"research"}),
            "ready_connectors": frozenset({"docs"}),
            "policy_fingerprint": "policy-v1",
        },
    }
    result = await runtime.execute(**kwargs)
    resumed = await runtime.execute(**kwargs)
    assert result == resumed and result.phase == "completed"
    assert calls == ["search", "summarize"]
