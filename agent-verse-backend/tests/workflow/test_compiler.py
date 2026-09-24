"""Tests for WorkflowCompiler — DSL to LangGraph graph compilation."""
from __future__ import annotations

import pytest

from app.workflow.compiler import CompiledWorkflow, WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition


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


def test_content_change_with_same_id_and_version_recompiles(
    compiler: WorkflowCompiler,
) -> None:
    """A workflow republished with edited steps but an unchanged ``version``
    string must still recompile — this is the real-world case for API/visual
    -builder workflows, whose DB mirror row hardcodes version="1.0.0" forever
    (see ``_WorkflowStore._bridge_upsert_definition``) and is never bumped on
    edit or publish. Nothing in the app calls ``WorkflowCompiler.invalidate()``
    on update/publish either. Without a content-based cache key, an operator's
    bug fix to a live workflow (e.g. fixing a conditional's routing target)
    would be silently ignored by the compiler for the life of the process —
    even for brand-new runs triggered long after the fix was published.
    """
    wf_before = WorkflowDefinition(
        id="wf-live",
        name="wf",
        version="1.0.0",
        steps=[StepDefinition(id="a", type="tool", tool="t")],
    )
    r1 = compiler.compile(wf_before)
    assert len(r1.definition.steps) == 1

    # Operator edits and republishes: same id, same version, different steps.
    wf_after = WorkflowDefinition(
        id="wf-live",
        name="wf",
        version="1.0.0",
        steps=[
            StepDefinition(id="a", type="tool", tool="t"),
            StepDefinition(id="b", type="tool", tool="t", depends_on=["a"]),
        ],
    )
    r2 = compiler.compile(wf_after)

    assert r2 is not r1
    assert r2.definition is wf_after
    assert len(r2.definition.steps) == 2


def test_invalidate_removes_all_versions(compiler: WorkflowCompiler) -> None:
    wf1 = WorkflowDefinition(name="v1", version="1",
                              steps=[StepDefinition(id="s", type="tool", tool="t")])
    wf2 = WorkflowDefinition(name="v1", id=wf1.id, version="2",
                              steps=[StepDefinition(id="s", type="tool", tool="t")])
    compiler.compile(wf1)
    compiler.compile(wf2)
    compiler.invalidate(wf1.id)
    assert all(not k.startswith(f"{wf1.id}:") for k in compiler._cache)


# ── Per-step resolved input capture (_step_input_payload) ─────────────────────


def test_step_input_payload_collects_generic_input() -> None:
    step = StepDefinition(id="s", type="ocr", input={"image_base64": "{{inputs.doc}}"})
    assert WorkflowCompiler._step_input_payload(step) == {"image_base64": "{{inputs.doc}}"}


def test_step_input_payload_collects_llm_prompt_and_http_fields() -> None:
    llm = StepDefinition(id="l", type="llm", prompt="Summarize {{inputs.text}}")
    assert WorkflowCompiler._step_input_payload(llm) == {"prompt": "Summarize {{inputs.text}}"}

    http = StepDefinition(
        id="h", type="http", url="{{inputs.cb}}", request_body={"x": "{{steps.a.output}}"}
    )
    payload = WorkflowCompiler._step_input_payload(http)
    assert payload["url"] == "{{inputs.cb}}"
    assert payload["request_body"] == {"x": "{{steps.a.output}}"}


def test_step_input_payload_empty_for_bare_step() -> None:
    # A conditional carries no input-bearing fields → empty payload (nothing to show).
    step = StepDefinition(id="c", type="conditional")
    assert WorkflowCompiler._step_input_payload(step) == {}


# ── _parse_step_timeout: '30s' / '5m' / '2h' / bare seconds / invalid → float ──


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 0.0),
        ("100ms", 0.1),
        ("30s", 30.0),
        ("5m", 300.0),
        ("2h", 7200.0),
        ("15", 15.0),
        ("not-a-duration", 0.0),
    ],
)
def test_parse_step_timeout(raw: str, expected: float) -> None:
    assert WorkflowCompiler._parse_step_timeout(raw) == pytest.approx(expected)


# ── _should_retry: RetryConfig.fail_on / retry_on exception-name filters ──────


def test_should_retry_defaults_true_with_no_filters() -> None:
    from app.workflow.dsl import RetryConfig

    assert WorkflowCompiler._should_retry(RetryConfig(), RuntimeError("x")) is True


def test_should_retry_false_when_exception_in_fail_on() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(fail_on=["ValueError"])
    assert WorkflowCompiler._should_retry(retry, ValueError("bad input")) is False
    # A different exception type is unaffected by fail_on.
    assert WorkflowCompiler._should_retry(retry, RuntimeError("transient")) is True


def test_should_retry_false_when_not_in_retry_on_allowlist() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(retry_on=["TimeoutError"])
    assert WorkflowCompiler._should_retry(retry, RuntimeError("not listed")) is False
    assert WorkflowCompiler._should_retry(retry, TimeoutError("listed")) is True


def test_should_retry_fail_on_takes_precedence_over_retry_on() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(retry_on=["ValueError"], fail_on=["ValueError"])
    assert WorkflowCompiler._should_retry(retry, ValueError("both lists")) is False


# ── _retry_delay: fixed / linear / exponential backoff ─────────────────────────


def test_retry_delay_fixed_backoff_is_constant() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(backoff="fixed", base_delay_ms=1000)
    assert WorkflowCompiler._retry_delay(retry, 1) == 1.0
    assert WorkflowCompiler._retry_delay(retry, 5) == 1.0


def test_retry_delay_linear_backoff_scales_with_attempt() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(backoff="linear", base_delay_ms=1000)
    assert WorkflowCompiler._retry_delay(retry, 1) == 1.0
    assert WorkflowCompiler._retry_delay(retry, 3) == 3.0


def test_retry_delay_exponential_backoff_doubles() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(backoff="exponential", base_delay_ms=1000)
    assert WorkflowCompiler._retry_delay(retry, 1) == 1.0
    assert WorkflowCompiler._retry_delay(retry, 2) == 2.0
    assert WorkflowCompiler._retry_delay(retry, 3) == 4.0


def test_retry_delay_negative_base_clamped_to_zero() -> None:
    from app.workflow.dsl import RetryConfig

    retry = RetryConfig(backoff="fixed", base_delay_ms=-500)
    assert WorkflowCompiler._retry_delay(retry, 1) == 0.0


# ── _find_downstream / _find_terminal_steps: depends_on_any, branch/action targets ──


def test_find_downstream_depends_on_any_matches_any_listed_dep() -> None:
    wf = WorkflowDefinition(
        name="fanin",
        steps=[
            StepDefinition(id="a", type="tool", tool="t"),
            StepDefinition(id="b", type="tool", tool="t"),
            StepDefinition(
                id="c", type="tool", tool="t", depends_on=["a", "b"], depends_on_any=True
            ),
        ],
    )
    # Both "a" and "b" individually route to "c" under depends_on_any semantics.
    assert WorkflowCompiler._find_downstream("a", wf) == ["c"]
    assert WorkflowCompiler._find_downstream("b", wf) == ["c"]


def test_find_downstream_depends_on_all_requires_full_dep_match() -> None:
    # Without depends_on_any, _find_downstream still lists "c" for each of its
    # individual deps (LangGraph's own edge semantics enforce the join), so this
    # documents that the helper doesn't distinguish AND vs ANY beyond the flag.
    wf = WorkflowDefinition(
        name="fanin2",
        steps=[
            StepDefinition(id="a", type="tool", tool="t"),
            StepDefinition(id="b", type="tool", tool="t"),
            StepDefinition(id="c", type="tool", tool="t", depends_on=["a", "b"]),
        ],
    )
    assert WorkflowCompiler._find_downstream("a", wf) == ["c"]


def test_find_terminal_steps_excludes_conditional_branch_targets() -> None:
    from app.workflow.dsl import ConditionalBranch

    wf = WorkflowDefinition(
        name="cond-terminal",
        steps=[
            StepDefinition(
                id="check",
                type="conditional",
                expression="1 == 1",
                branches=[
                    ConditionalBranch(condition="1 == 1", next="yes"),
                    ConditionalBranch(condition="else", next="no"),
                ],
            ),
            StepDefinition(id="yes", type="tool", tool="t"),
            StepDefinition(id="no", type="tool", tool="t"),
        ],
    )
    # "yes"/"no" are reached only via branch targets (no depends_on), but must
    # still be excluded from the terminal set — otherwise the compiler would
    # wire them to BOTH their branch edge and END, corrupting routing.
    terminal = WorkflowCompiler._find_terminal_steps(wf)
    assert "yes" not in terminal
    assert "no" not in terminal


def test_find_terminal_steps_excludes_hitl_action_targets() -> None:
    from app.workflow.dsl import HITLAction

    wf = WorkflowDefinition(
        name="hitl-terminal",
        steps=[
            StepDefinition(
                id="gate",
                type="hitl",
                actions=[
                    HITLAction(id="approve", next="done"),
                    HITLAction(id="reject"),
                ],
            ),
            StepDefinition(id="done", type="tool", tool="t"),
        ],
    )
    terminal = WorkflowCompiler._find_terminal_steps(wf)
    assert "done" not in terminal
    # Nothing depends_on "gate" itself, so the helper (which only looks at
    # depends_on plus branch/action *targets*) still reports it terminal here;
    # _compile_uncached separately wires "gate" via add_conditional_edges.
    assert "gate" in terminal
