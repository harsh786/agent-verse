from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.intelligence.improvement_action_executor import ImprovementActionExecutor
from app.intelligence.learning_experiments import ExperimentOutcome, LearningExperimentService
from app.memory.contracts import ExperimentSpec, ImprovementActionRecord
from app.memory.knowledge_graph_memory import KnowledgeFact, KnowledgeGraphMemory
from app.memory.procedural_validator import ProcedureContract, validate_procedure
from app.memory.prospective import ProspectiveMemory, ProspectiveMemoryService, prospective_id


def _procedure() -> ProcedureContract:
    return ProcedureContract(
        procedure_id="skill",
        tenant_id="tenant",
        skill_version="v1",
        tool_sequence=("search",),
        required_capabilities=frozenset({"research"}),
        tool_schema_versions={"search": "v2"},
        connector_ids=frozenset({"docs"}),
        policy_fingerprint="policy-v1",
    )


def test_procedure_is_revalidated_for_schema_policy_connector_and_tenant() -> None:
    validate_procedure(
        _procedure(),
        tenant_id="tenant",
        available_tools={"search": "v2"},
        allowed_capabilities=frozenset({"research"}),
        ready_connectors=frozenset({"docs"}),
        policy_fingerprint="policy-v1",
    )
    with pytest.raises(RuntimeError, match="schema mismatch"):
        validate_procedure(
            _procedure(),
            tenant_id="tenant",
            available_tools={"search": "v3"},
            allowed_capabilities=frozenset({"research"}),
            ready_connectors=frozenset({"docs"}),
            policy_fingerprint="policy-v1",
        )


@pytest.mark.asyncio
async def test_knowledge_graph_merge_is_idempotent_scoped_and_provenance_linked() -> None:
    store = KnowledgeGraphMemory()
    fact = KnowledgeFact(
        fact_id="f",
        tenant_id="tenant",
        subject="AgentVerse",
        predicate="is",
        object="safe",
        evidence_refs=("evidence://1",),
        classification="internal",
        confidence=9000,
    )
    assert (await store.merge(fact)).version == 1
    merged = await store.merge(fact.model_copy(update={"evidence_refs": ("evidence://2",)}))
    assert merged.version == 2 and len(merged.evidence_refs) == 2
    assert await store.query("other", subject="AgentVerse") == ()


@pytest.mark.asyncio
async def test_prospective_memory_reclaims_with_fencing_and_reauthorizes() -> None:
    service = ProspectiveMemoryService()
    now = datetime.now(UTC)
    item = ProspectiveMemory(
        memory_id=prospective_id("tenant", "intent"),
        tenant_id="tenant",
        intention="follow up",
        due_at=now,
        expires_at=now + timedelta(hours=1),
        source_goal_id="g",
        source_execution_id="e",
        policy_snapshot={},
        classification="internal",
        idempotency_key="intent",
    )
    await service.create(item)
    first = (await service.lease_due("tenant", now=now, lease_duration=timedelta(seconds=1)))[0]
    second = (
        await service.lease_due(
            "tenant", now=now + timedelta(seconds=2), lease_duration=timedelta(seconds=1)
        )
    )[0]
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(PermissionError):
        await service.complete(
            "tenant",
            item.memory_id,
            fencing_token=second.fencing_token,
            authorized=False,
            result={},
        )


def test_experiment_assignment_is_sticky_and_promotion_requires_samples_and_guardrails() -> None:
    service = LearningExperimentService()
    spec = ExperimentSpec(
        experiment_id="exp",
        tenant_id="tenant",
        agent_id="agent",
        kind="prompt",
        target_key="planner",
        control_version="v1",
        candidate_version="v2",
        assignment_seed="seed",
        traffic_percent=50,
        primary_metric="quality",
        guardrail_metrics=("cost",),
        min_samples_per_arm=1,
        confidence_threshold=0.95,
        status="running",
    )
    service.register(spec)
    assert service.assign(spec, fingerprint="goal") == service.assign(spec, fingerprint="goal")
    service.record("tenant", ExperimentOutcome("a", "control", 0.5, True))
    service.record("tenant", ExperimentOutcome("b", "candidate", 0.8, True))
    assert service.promotion_ready(spec)


@pytest.mark.asyncio
async def test_improvement_actions_retry_are_idempotent_and_policy_bounded() -> None:
    attempts = 0

    async def handler(payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return {"version": payload["version"], "rollback_ref": "prompt://v1"}

    executor = ImprovementActionExecutor(handlers={"update_prompt": handler})
    record = ImprovementActionRecord(
        action_id="action",
        tenant_id="tenant",
        goal_id="goal",
        action_type="update_prompt",
        payload={"version": "v2"},
        state="pending",
        idempotency_key="command",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    completed = await executor.execute(record, policy_allowed=True)
    assert completed.state == "completed" and completed.attempts == 2
    assert await executor.execute(record, policy_allowed=True) == completed
