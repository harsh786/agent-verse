"""2.W-7: the compiler node wrapper must enforce the DSL's ``retry``,
per-step ``timeout``, and ``on_failure``/``on_failure_default`` fields.

Today these fields are parsed into ``StepDefinition`` but the compiler's
``_build_node_fn`` calls ``node.execute`` exactly once and lets any exception
propagate — so ``retry.max_attempts`` never retries, ``timeout`` never bounds a
step, and ``on_failure`` never routes. These tests drive ``_build_node_fn``
directly with fake step nodes and assert the enforcement.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.dsl import RetryConfig, StepDefinition
from app.workflow.registry import StepTypeRegistry
from app.workflow.state import WorkflowRunStatus

pytestmark = pytest.mark.asyncio


def _register(step_type: str, node_cls: type) -> None:
    # node_fn resolves via StepTypeRegistry.get() only, so inserting into the
    # class map directly avoids needing a StepTypeMeta just for the test.
    StepTypeRegistry._registry[step_type] = node_cls  # type: ignore[attr-defined,assignment]


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    # Snapshot and restore the private registry so fake types don't leak.
    saved = dict(StepTypeRegistry._registry)  # type: ignore[attr-defined]
    try:
        yield
    finally:
        StepTypeRegistry._registry = saved  # type: ignore[attr-defined]


def _compiler() -> WorkflowCompiler:
    return WorkflowCompiler(context_resolver=MagicMock())


def _step(step_type: str, **kw: Any) -> StepDefinition:
    return StepDefinition(id="s1", type=step_type, **kw)


async def test_retry_runs_until_success() -> None:
    calls = {"n": 0}

    class _FlakyNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return {"step_outputs": {"s1": {"ok": True}}}

    _register("flaky", _FlakyNode)
    step = _step("flaky", retry=RetryConfig(max_attempts=3, base_delay_ms=0))
    node_fn = _compiler()._build_node_fn(step)

    result = await node_fn({})
    assert calls["n"] == 3, "should run once + retry twice before succeeding"
    assert result["step_outputs"]["s1"] == {"ok": True}


async def test_timeout_routes_to_on_failure_use_default() -> None:
    import asyncio

    class _SlowNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(5)
            return {"step_outputs": {"s1": {"never": True}}}

    _register("slow", _SlowNode)
    step = _step(
        "slow",
        timeout="0.05s",
        on_failure="use_default",
        on_failure_default={"fallback": True},
    )
    node_fn = _compiler()._build_node_fn(step)

    result = await node_fn({})
    assert result["step_outputs"]["s1"] == {"fallback": True}


async def test_on_failure_skip_continues_without_raising() -> None:
    class _BoomNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("boom")

    _register("boom_skip", _BoomNode)
    step = _step("boom_skip", on_failure="skip")
    node_fn = _compiler()._build_node_fn(step)

    result = await node_fn({})
    assert result["step_outputs"]["s1"]["_skipped"] is True
    assert "status" not in result or result["status"] != WorkflowRunStatus.PAUSED


async def test_on_failure_abort_raises() -> None:
    class _BoomNode:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("fatal")

    _register("boom_abort", _BoomNode)
    step = _step("boom_abort", on_failure="abort")
    node_fn = _compiler()._build_node_fn(step)

    with pytest.raises(RuntimeError, match="fatal"):
        await node_fn({})


async def test_retry_exhaustion_routes_to_pause() -> None:
    calls = {"n": 0}

    class _AlwaysFail:
        def __init__(self, step: Any, ctx: Any, **services: Any) -> None:
            pass

        async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
            calls["n"] += 1
            raise RuntimeError("still failing")

    _register("always_fail", _AlwaysFail)
    step = _step(
        "always_fail",
        retry=RetryConfig(max_attempts=2, base_delay_ms=0),
        on_failure="pause",
    )
    node_fn = _compiler()._build_node_fn(step)

    result = await node_fn({})
    assert calls["n"] == 2, "max_attempts=2 → two total attempts"
    assert result["status"] == WorkflowRunStatus.PAUSED
    assert result["paused_by"] == "step_failure:s1"
    assert result["error_step_id"] == "s1"
