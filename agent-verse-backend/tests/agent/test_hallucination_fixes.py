"""Unit tests verifying all 6 hallucination-elimination fixes."""


# ── Vector 5: Grounded executor prompt ───────────────────────────────────────


def _agent_source() -> str:
    """Read combined source of graph.py and all node mixin files."""
    import pathlib
    parts = [pathlib.Path("app/agent/graph.py").read_text(encoding="utf-8")]
    for f in sorted(pathlib.Path("app/agent/nodes").glob("*.py")):
        parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)

def test_executor_system_contains_grounding_rules():
    """EXECUTOR_SYSTEM must contain all 5 grounding rules."""
    from app.agent.prompts import EXECUTOR_SYSTEM

    required_phrases = [
        "NEVER fabricate",
        "NEVER claim",
        "INSUFFICIENT DATA",
        "ONLY JSON",
        "No markdown",
    ]
    for phrase in required_phrases:
        assert phrase in EXECUTOR_SYSTEM, (
            f"EXECUTOR_SYSTEM is missing grounding rule: '{phrase}'\n"
            f"Current content:\n{EXECUTOR_SYSTEM}"
        )


# ── Vector 1: Tool name validation ───────────────────────────────────────────

def test_validate_tool_name_rejects_unknown():
    """validate_tool_name must return a rejection string for unlisted tool names."""
    from app.agent.tool_calls import validate_tool_name

    allowed = {"jira_server.jira_search_issues", "builtin-confluence.confluence_create_page"}
    result = validate_tool_name("jira_server.jira_update_sprint_velocity", allowed)

    assert result is not None, "Must return rejection message for unknown tool"
    _rl = result.lower()
    assert "not available" in _rl or "unknown" in _rl or "not in" in _rl, (
        f"Rejection message must explain the tool is not available. Got: {result}"
    )
    assert "jira_update_sprint_velocity" in result, (
        "Rejection must name the bad tool"
    )


def test_validate_tool_name_accepts_known():
    """validate_tool_name must return None for tools in the allowed set."""
    from app.agent.tool_calls import validate_tool_name

    allowed = {"jira_server.jira_search_issues", "builtin-confluence.confluence_create_page"}
    result = validate_tool_name("jira_server.jira_search_issues", allowed)

    assert result is None, "Must return None when tool is known"


def test_validate_tool_name_accepts_rpa_tools():
    """validate_tool_name must always accept built-in RPA tools regardless of allowed set."""
    from app.agent.tool_calls import validate_tool_name

    allowed: set[str] = set()  # empty — no MCP tools
    result = validate_tool_name("rpa_open_url", allowed)
    assert result is None, "RPA tools must always be accepted"


# ── Vector 3: Executor context limit ─────────────────────────────────────────

def test_executor_context_limit_is_larger_than_sse_limit():
    """Executor LLM context limit must be >= 5000 chars."""
    from app.agent.sanitization import (
        _EXECUTOR_CONTEXT_MAX_LENGTH,
        _TOOL_EVENT_MAX_LENGTH,
    )
    assert _EXECUTOR_CONTEXT_MAX_LENGTH >= 5000, (
        f"Executor context limit must be >= 5000, got {_EXECUTOR_CONTEXT_MAX_LENGTH}"
    )
    assert _EXECUTOR_CONTEXT_MAX_LENGTH > _TOOL_EVENT_MAX_LENGTH, (
        "Executor context limit must be larger than SSE event limit"
    )


def test_sanitize_tool_raw_output_respects_custom_max_length():
    """sanitize_tool_raw_output must respect an explicit max_length override."""
    from app.agent.sanitization import sanitize_tool_raw_output

    long_text = "x" * 6000
    result = sanitize_tool_raw_output(long_text, max_length=5000)
    assert len(result) <= 5000 + len("...[truncated]"), (
        "Output must be capped at max_length + marker"
    )
    assert "...[truncated]" in result


def test_sanitize_tool_raw_output_uses_1000_default():
    """Default max_length is 1000 for backward compat (SSE events)."""
    from app.agent.sanitization import sanitize_tool_raw_output

    long_text = "x" * 2000
    result = sanitize_tool_raw_output(long_text)
    assert len(result) <= 1000 + len("...[truncated]")


# ── Vector 2: Full failed-step visibility for verifier ───────────────────────

def test_verifier_summary_includes_all_failed_steps():
    """When >5 steps with early failures, verifier must see ALL failed steps."""
    from unittest.mock import MagicMock

    # Create 8 steps: step 2 fails early, steps 6-8 are the last 3 fine ones
    steps = []
    for i in range(1, 9):
        s = MagicMock()
        s.description = f"Step {i}"
        s.output = f"output {i}"
        s.error = f"Error in step {i}" if i == 2 else None
        s.tool_calls = []
        steps.append(s)

    from app.agent.nodes._helpers import _build_verifier_summary
    summary = _build_verifier_summary(steps)

    assert "Step 2" in summary, "Failed step 2 must appear even though not in last 5"
    assert "Error in step 2" in summary, "Error message must be in summary"
    assert "FAILED STEPS" in summary.upper() or "[STEP ERROR]" in summary


# ── Vector 4: Argument schema validation ─────────────────────────────────────

def test_validate_tool_arguments_catches_missing_required():
    """validate_tool_arguments must reject calls missing required fields."""
    from app.agent.tool_calls import validate_tool_arguments

    schema = {
        "type": "object",
        "properties": {
            "jql": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["jql"],
    }

    errors = validate_tool_arguments({"max_results": 10}, schema)
    assert len(errors) >= 1
    assert any("jql" in e for e in errors), f"Must mention missing field 'jql'. Got: {errors}"


def test_validate_tool_arguments_catches_unknown_fields():
    """validate_tool_arguments must flag arguments not in schema properties."""
    from app.agent.tool_calls import validate_tool_arguments

    schema = {
        "type": "object",
        "properties": {"jql": {"type": "string"}},
        "required": ["jql"],
    }

    errors = validate_tool_arguments({"jql": "project=X", "nonexistent_field": "oops"}, schema)
    assert any("nonexistent_field" in e for e in errors), (
        f"Must flag unknown field. Got: {errors}"
    )


def test_validate_tool_arguments_passes_valid_call():
    """validate_tool_arguments must return empty list for valid arguments."""
    from app.agent.tool_calls import validate_tool_arguments

    schema = {
        "type": "object",
        "properties": {
            "jql": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["jql"],
    }

    errors = validate_tool_arguments({"jql": "project=X AND status=Open"}, schema)
    assert errors == [], f"No errors expected for valid args. Got: {errors}"


def test_validate_tool_arguments_handles_missing_schema():
    """validate_tool_arguments must return empty list when schema is None/empty."""
    from app.agent.tool_calls import validate_tool_arguments

    assert validate_tool_arguments({"any": "thing"}, None) == []
    assert validate_tool_arguments({"any": "thing"}, {}) == []


# ── Vector 6: Separate verifier provider ─────────────────────────────────────

def test_agentgraph_accepts_separate_verifier():
    """AgentGraph must store planner, executor, verifier as distinct attributes."""
    from unittest.mock import MagicMock

    from app.agent.graph import AgentGraph

    planner = MagicMock()
    executor = MagicMock()
    verifier = MagicMock()

    graph = AgentGraph(
        planner=planner,
        executor=executor,
        verifier=verifier,
    )

    assert graph._planner is planner
    assert graph._executor is executor
    assert graph._verifier is verifier


def test_build_verifier_provider_is_callable_in_main():
    """_build_verifier_provider must be importable from app.main."""
    from app.main import _build_verifier_provider
    assert callable(_build_verifier_provider)


def test_self_optimizer_threshold_is_half():
    """Self-optimizer must only fire on failing goals (< 0.5), not all goals."""
    src = _agent_source()
    # Find lines with average_score() threshold comparisons
    lines = [ln.strip() for ln in src.splitlines() if "average_score()" in ln and "< " in ln]
    threshold_lines = [ln for ln in lines if "0." in ln]
    assert any("0.5" in ln or "0.50" in ln for ln in threshold_lines), (
        f"Self-optimizer threshold must be 0.5 (not 1.0). Found: {threshold_lines}"
    )
    # Confirm 1.0 is NOT the threshold
    assert not any(
        ln.strip() == "and scorecard.average_score() < 1.0" for ln in src.splitlines()
    ), (
        "Threshold of 1.0 would fire on every non-perfect goal — must be 0.5"
    )
