"""Tests for WorkflowCompiler — DSL to LangGraph graph compilation."""
from __future__ import annotations

import pytest

from app.workflow.compiler import CompiledWorkflow, WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.registry import StepTypeRegistry


@pytest.fixture
def compiler() -> WorkflowCompiler:
    ctx = ContextResolver()
    return WorkflowCompiler(context_resolver=ctx)


def _simple_wf(**kwargs) -> WorkflowDefinition:
    defaults = dict(
        name="test",
        steps=[StepDefinition(id="s1", type="tool", tool="test.tool")],
    )
    defaults.update(kwargs)
    return WorkflowDefinition(**defaults)


def test_compile_returns_compiled_workflow(compiler: WorkflowCompiler) -> None:
    wf = _simple_wf()
    result = compiler.compile(wf)
    assert isinstance(result, CompiledWorkflow)
    assert result.definition is wf


def test_compile_caches_result(compiler: WorkflowCompiler) -> None:
    """Second call with same workflow_id+version returns cached result."""
    wf = _simple_wf()
    r1 = compiler.compile(wf)
    r2 = compiler.compile(wf)
    assert r1 is r2


def test_invalidate_clears_cache(compiler: WorkflowCompiler) -> None:
    wf = _simple_wf()
    r1 = compiler.compile(wf)
    compiler.invalidate(wf.id)
    r2 = compiler.compile(wf)
    assert r1 is not r2  # fresh compile after invalidation


def test_compile_multi_step_linear(compiler: WorkflowCompiler) -> None:
    wf = WorkflowDefinition(
        name="linear",
        steps=[
            StepDefinition(id="a", type="tool", tool="t"),
            StepDefinition(id="b", type="tool", tool="t", depends_on=["a"]),
            StepDefinition(id="c", type="tool", tool="t", depends_on=["b"]),
        ],
    )
    result = compiler.compile(wf)
    assert isinstance(result, CompiledWorkflow)


def test_compile_empty_steps(compiler: WorkflowCompiler) -> None:
    """Empty workflow raises ValueError from LangGraph (no entry point)."""
    wf = WorkflowDefinition(name="empty", steps=[])
    with pytest.raises(ValueError, match="entrypoint"):
        compiler.compile(wf)


def test_compile_conditional_step(compiler: WorkflowCompiler) -> None:
    from app.workflow.dsl import ConditionalBranch
    wf = WorkflowDefinition(
        name="cond",
        steps=[
            StepDefinition(
                id="check",
                type="conditional",
                expression="1 == 1",
                branches=[
                    ConditionalBranch(condition="1 == 1", next="end_yes"),
                    ConditionalBranch(condition="else", next="end_no"),
                ],
            ),
            StepDefinition(id="end_yes", type="tool", tool="t", depends_on=["check"]),
            StepDefinition(id="end_no", type="tool", tool="t", depends_on=["check"]),
        ],
    )
    result = compiler.compile(wf)
    assert isinstance(result, CompiledWorkflow)


def test_compile_parallel_step(compiler: WorkflowCompiler) -> None:
    wf = WorkflowDefinition(
        name="parallel",
        steps=[
            StepDefinition(
                id="par",
                type="parallel",
                parallel_branches=[
                    StepDefinition(id="b1", type="tool", tool="t"),
                    StepDefinition(id="b2", type="tool", tool="t"),
                ],
            ),
            StepDefinition(id="merge", type="tool", tool="t", depends_on=["par"]),
        ],
    )
    result = compiler.compile(wf)
    assert isinstance(result, CompiledWorkflow)


def test_different_versions_different_cache_keys(compiler: WorkflowCompiler) -> None:
    """Two different versions compile independently and cache separately."""
    wf1 = WorkflowDefinition(name="v1", version="1",
                              steps=[StepDefinition(id="s", type="tool", tool="t")])
    wf2 = WorkflowDefinition(name="v2", id=wf1.id, version="2",
                              steps=[StepDefinition(id="s", type="tool", tool="t")])
    r1 = compiler.compile(wf1)
    r2 = compiler.compile(wf2)
    assert r1 is not r2


def test_invalidate_removes_all_versions(compiler: WorkflowCompiler) -> None:
    wf1 = WorkflowDefinition(name="v1", version="1",
                              steps=[StepDefinition(id="s", type="tool", tool="t")])
    wf2 = WorkflowDefinition(name="v1", id=wf1.id, version="2",
                              steps=[StepDefinition(id="s", type="tool", tool="t")])
    compiler.compile(wf1)
    compiler.compile(wf2)
    compiler.invalidate(wf1.id)
    assert all(not k.startswith(f"{wf1.id}:") for k in compiler._cache)
