"""Test LLM judge uses actual agent output not goal prompt (FIX 0.10)."""
from app.intelligence.eval_suite import GoldenTaskResult


def test_golden_task_result_has_actual_output():
    r = GoldenTaskResult(
        task_id="t1", goal="Find Jira tickets", passed=True,
        actual_output="Found 12 open tickets: ABC-1, ABC-2...",
    )
    assert r.actual_output == "Found 12 open tickets: ABC-1, ABC-2..."
    assert r.goal != r.actual_output


def test_actual_output_defaults_empty():
    r = GoldenTaskResult(task_id="t1", goal="test", passed=False)
    assert r.actual_output == ""


def test_judge_uses_actual_output_not_goal():
    """run_with_llm_judge must use task_result.actual_output."""
    import pathlib
    src = pathlib.Path("app/intelligence/eval_suite.py").read_text()
    assert "task_result.actual_output" in src or "actual_output or task_result.goal" in src, (
        "run_with_llm_judge must use task_result.actual_output, not task_result.goal"
    )


def test_run_task_returns_actual_output():
    """_run_task return must include actual_output=all_output."""
    import pathlib
    src = pathlib.Path("app/intelligence/eval_suite.py").read_text()
    assert "actual_output=all_output" in src, (
        "_run_task must return actual_output=all_output in GoldenTaskResult"
    )
