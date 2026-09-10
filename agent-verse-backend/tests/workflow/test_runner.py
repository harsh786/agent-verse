"""Tests for WorkflowRunner — trigger validation and dispatch logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import InputDefinition, WorkflowDefinition
from app.workflow.runner import WorkflowRunner, WorkflowValidationError


@pytest.fixture
def runner() -> WorkflowRunner:
    ctx = ContextResolver()
    compiler = WorkflowCompiler(context_resolver=ctx)
    return WorkflowRunner(compiler=compiler)


def _wf(inputs: dict | None = None, **kwargs) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="test",
        inputs=inputs or {},
        **kwargs,
    )


# ── Input validation ──────────────────────────────────────────────────────────


def test_validate_inputs_missing_required() -> None:
    wf = WorkflowDefinition(
        name="req",
        inputs={"email": InputDefinition(type="string", required=True)},
        steps=[],
    )
    runner = WorkflowRunner(compiler=MagicMock())
    with pytest.raises(WorkflowValidationError, match="email"):
        runner._validate_inputs(wf, {})


def test_validate_inputs_required_with_default_ok() -> None:
    wf = WorkflowDefinition(
        name="def",
        inputs={"count": InputDefinition(type="number", required=True, default=5)},
        steps=[],
    )
    runner = WorkflowRunner(compiler=MagicMock())
    runner._validate_inputs(wf, {})  # no exception


def test_validate_inputs_enum_check() -> None:
    wf = WorkflowDefinition(
        name="enum",
        inputs={"env": InputDefinition(type="string", enum=["prod", "staging"])},
        steps=[],
    )
    runner = WorkflowRunner(compiler=MagicMock())
    with pytest.raises(WorkflowValidationError, match="not in enum"):
        runner._validate_inputs(wf, {"env": "dev"})


def test_validate_inputs_enum_valid() -> None:
    wf = WorkflowDefinition(
        name="enum",
        inputs={"env": InputDefinition(type="string", enum=["prod", "staging"])},
        steps=[],
    )
    runner = WorkflowRunner(compiler=MagicMock())
    runner._validate_inputs(wf, {"env": "prod"})  # ok


def test_validate_inputs_no_required_empty_ok() -> None:
    wf = WorkflowDefinition(name="optional", inputs={}, steps=[])
    runner = WorkflowRunner(compiler=MagicMock())
    runner._validate_inputs(wf, {})  # no exception


# ── Payload size limit ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_too_large_raises(runner: WorkflowRunner) -> None:
    # Simulate a workflow (no store)
    big_payload = {"data": "x" * (2 * 1024 * 1024)}  # 2 MB
    run_store_mock = AsyncMock()
    run_store_mock.get_definition = AsyncMock(return_value=None)
    runner._run_store = None  # no store

    # Should raise before dispatching
    with pytest.raises(WorkflowValidationError, match="payload"):
        await runner.run(
            workflow_id="wf-1",
            tenant_id="t-1",
            inputs=big_payload,
        )


# ── Plan tier routing ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_plan_tier_defaults_to_free(runner: WorkflowRunner) -> None:
    tier = await runner._get_plan_tier("tenant-xyz")
    assert tier == "free"


# ── WorkflowRunner construction ───────────────────────────────────────────────


def test_runner_builds_without_optional_services() -> None:
    ctx = ContextResolver()
    compiler = WorkflowCompiler(context_resolver=ctx)
    r = WorkflowRunner(compiler=compiler)
    assert r._compiler is compiler
    assert r._run_store is None
    assert r._celery is None


def test_runner_accepts_extra_services() -> None:
    ctx = ContextResolver()
    compiler = WorkflowCompiler(context_resolver=ctx)
    fake_store = MagicMock()
    r = WorkflowRunner(compiler=compiler, run_store=fake_store)
    assert r._run_store is fake_store
