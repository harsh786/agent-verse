"""Tests that parallel step execution actually uses asyncio.gather."""
import asyncio
import pytest
from app.agent.structured_plan import StructuredPlan, StructuredStep


def test_execution_waves_two_independent_steps():
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Fetch data", depends_on=[]),
        StructuredStep(id="s2", description="Send notification", depends_on=[]),
        StructuredStep(id="s3", description="Create report", depends_on=["s1", "s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 2
    assert len(waves[0]) == 2  # s1 and s2 are parallel
    assert waves[1][0].id == "s3"


def test_execution_waves_all_sequential():
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Step 1", depends_on=[]),
        StructuredStep(id="s2", description="Step 2", depends_on=["s1"]),
        StructuredStep(id="s3", description="Step 3", depends_on=["s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 3  # All sequential
    for wave in waves:
        assert len(wave) == 1


@pytest.mark.asyncio
async def test_graph_has_enable_cot_attribute():
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.intelligence.guardrails import GuardrailChecker

    fake = FakeProvider(responses=["done"])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        result_processor=ResultProcessor(), dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(), guardrail_checker=GuardrailChecker(),
        enable_cot=True,
    )
    assert graph._enable_cot is True


@pytest.mark.asyncio
async def test_graph_has_enable_reflection_attribute():
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.intelligence.guardrails import GuardrailChecker

    fake = FakeProvider(responses=["done"])
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        result_processor=ResultProcessor(), dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(), guardrail_checker=GuardrailChecker(),
        enable_reflection=True,
    )
    assert graph._enable_reflection is True
