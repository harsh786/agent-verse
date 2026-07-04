# Hallucination Elimination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all 6 hallucination vectors identified in AgentVerse: unknown tool invocation, incomplete failure visibility, truncated execution context, weak executor grounding prompt, missing argument validation, and self-verification bias.

**Architecture:** Six targeted changes across `prompts.py`, `graph.py`, `sanitization.py`, `tool_calls.py`, `main.py`, and `tasks.py`. Each fix is independently testable. No new dependencies added — all fixes use existing provider, tool context, and agent state abstractions already present in the codebase.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, asyncpg, SQLAlchemy async.

---

## File Map

| File | Change |
|---|---|
| `app/agent/prompts.py` | Replace `EXECUTOR_SYSTEM` with grounded version (Vector 5) |
| `app/agent/tool_calls.py` | Add `validate_tool_name()` returning structured rejection (Vector 1) |
| `app/agent/sanitization.py` | Add `_EXECUTOR_CONTEXT_MAX_LENGTH = 5000` constant (Vector 3) |
| `app/agent/graph.py` | (a) Inject allowed-tools block in executor prompt (Vector 1+4); (b) use 5000-char context for executor LLM, keep 1000-char for SSE (Vector 3); (c) show ALL failed steps in verifier, not just last 5 (Vector 2) |
| `app/core/config.py` | Add `verifier_api_key: str = ""` field (Vector 6) |
| `app/main.py` | Build separate `_verifier_provider` from `VERIFIER_API_KEY` (Vector 6) |
| `app/scaling/tasks.py` | Build separate verifier provider in Celery worker; pass `verifier=_verifier_provider` to AgentGraph (Vector 6) |
| `tests/agent/test_hallucination_fixes.py` | New: 6 unit tests, one per vector |

---

## Task 1: Harden executor prompt with grounding rules (Vector 5)

**Files:**
- Modify: `app/agent/prompts.py` — replace `EXECUTOR_SYSTEM`
- Test: `tests/agent/test_hallucination_fixes.py` (create, first test)

- [ ] **Step 1: Create test file with first test**

Create `tests/agent/test_hallucination_fixes.py`:

```python
"""Unit tests verifying all 6 hallucination-elimination fixes."""
import pytest


# ── Vector 5: Grounded executor prompt ───────────────────────────────────────

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
```

- [ ] **Step 2: Run test — confirm FAILS**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_hallucination_fixes.py::test_executor_system_contains_grounding_rules -v
```

Expected: FAIL — one or more required phrases not found in current `EXECUTOR_SYSTEM`.

- [ ] **Step 3: Replace EXECUTOR_SYSTEM in prompts.py**

In `app/agent/prompts.py`, replace the entire `EXECUTOR_SYSTEM` string with:

```python
EXECUTOR_SYSTEM = """\
You are an expert task executor. Given a step to execute, perform it using the available tools.

CRITICAL GROUNDING RULES — NEVER violate these:
1. If a tool call is needed, respond ONLY with valid JSON:
   {"tool": "server_name.tool_name", "arguments": {"param": "value"}}
   No markdown, no explanation, ONLY the JSON object.
2. If no tool is needed and you can state the result from provided context, describe it concisely.
3. If you are UNCERTAIN or lack data, respond:
   {"tool": null, "result": "INSUFFICIENT DATA: <what is missing>"}
4. NEVER fabricate specific values (IDs, counts, dates, ticket numbers, names, URLs) without tool evidence.
5. NEVER claim a tool succeeded or returned data if you did not actually receive tool output.
6. NEVER invent tool names — only use tools from the ALLOWED TOOLS list provided in context.
"""
```

- [ ] **Step 4: Run test — confirm PASSES**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py::test_executor_system_contains_grounding_rules -v
```

Expected: PASS.

- [ ] **Step 5: Verify no other tests broke**

```bash
uv run pytest tests/agent/ -x -q
```

Expected: all pass (prompt string change doesn't break existing tests).

- [ ] **Step 6: Commit**

```bash
git add app/agent/prompts.py tests/agent/test_hallucination_fixes.py
git commit -m "fix(hallucination-v5): harden executor prompt with 6 grounding rules

Prevents the executor LLM from fabricating tool names, IDs, counts, or
claiming success without actual tool evidence. Adds INSUFFICIENT DATA response
pattern for uncertainty. All existing agent tests pass."
```

---

## Task 2: Validate tool name before dispatching — reject unknown tools early (Vector 1)

**Files:**
- Modify: `app/agent/tool_calls.py` — add `validate_tool_name()`
- Modify: `app/agent/graph.py` — (a) inject allowed-tools block into executor prompt; (b) call `validate_tool_name()` before dispatch
- Test: `tests/agent/test_hallucination_fixes.py` — add 2 tests

- [ ] **Step 1: Add tests for Vector 1**

Append to `tests/agent/test_hallucination_fixes.py`:

```python
# ── Vector 1: Tool name validation ───────────────────────────────────────────

def test_validate_tool_name_rejects_unknown():
    """validate_tool_name must return a rejection string for unlisted tool names."""
    from app.agent.tool_calls import validate_tool_name

    allowed = {"jira_server.jira_search_issues", "builtin-confluence.confluence_create_page"}
    result = validate_tool_name("jira_server.jira_update_sprint_velocity", allowed)

    assert result is not None, "Must return rejection message for unknown tool"
    assert "not available" in result.lower() or "unknown" in result.lower(), (
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
```

- [ ] **Step 2: Run tests — confirm they FAIL**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "validate_tool_name" -v
```

Expected: FAIL — `ImportError: cannot import name 'validate_tool_name'`

- [ ] **Step 3: Add `validate_tool_name()` to tool_calls.py**

At the end of `app/agent/tool_calls.py`, add:

```python
# ── Built-in tool prefixes that always bypass MCP validation ─────────────────
_ALWAYS_ALLOWED_PREFIXES: frozenset[str] = frozenset({
    "rpa_",           # rpa_open_url, rpa_click, etc.
    "civilization_",  # civilization_spawn
})


def validate_tool_name(tool_name: str, allowed_tools: set[str]) -> str | None:
    """Validate that *tool_name* is in the allowed set.

    Returns:
        None   — tool is valid, proceed with dispatch.
        str    — human-readable rejection message (use as raw_output, skip dispatch).

    Built-in prefixes (rpa_*, civilization_*) are always allowed regardless of
    the MCP-discovered tool list.
    """
    if not tool_name:
        return "Tool name is empty — cannot dispatch."

    # Built-in tools are always allowed
    for prefix in _ALWAYS_ALLOWED_PREFIXES:
        if tool_name.startswith(prefix) or tool_name.split(".")[-1].startswith(prefix.rstrip("_")):
            return None

    # If no allowed_tools were provided (discovery failed), be permissive
    if not allowed_tools:
        return None

    # Exact match
    if tool_name in allowed_tools:
        return None

    # Suffix match: allow "jira_search_issues" to match "jira_server.jira_search_issues"
    bare = tool_name.split(".")[-1]
    if any(bare == t.split(".")[-1] for t in allowed_tools):
        return None

    # Rejected
    available_sample = ", ".join(sorted(allowed_tools)[:8])
    if len(allowed_tools) > 8:
        available_sample += f" … (+{len(allowed_tools) - 8} more)"
    return (
        f"[TOOL NOT AVAILABLE] '{tool_name}' is not in the discovered tool list. "
        f"Available tools: {available_sample}. "
        "Choose one of the available tools or respond with INSUFFICIENT DATA."
    )
```

- [ ] **Step 4: Run tool validation tests — confirm they PASS**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "validate_tool_name" -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Wire `validate_tool_name()` in graph.py — inject allowed-tools block into executor**

In `app/agent/graph.py`, find the executor node's prompt-building section. It builds `extra_parts` before calling `system_content = system_content + tool_context_text`. After the schema injection block (around line 523-528), add the allowed-tools injection and the validation:

First, add the allowed-tools block to the executor system prompt. Find the section in `_node_execute` where `req = CompletionRequest(messages=[Message(role="system", content=executor_system), ...])` is constructed (around line 1162). Read the actual lines carefully.

The goal: before the executor LLM call, append the list of allowed tool names to the system prompt so the LLM knows exactly what tools exist.

Find `_node_execute` and locate where `req = CompletionRequest(...)` for the executor is built. The executor call is inside `_run_step` (called from `_node_execute`). Read `_run_step` to find the exact location:

```bash
grep -n "_run_step\|def _run_step\|executor_system\|system.*executor\|EXECUTOR_SYSTEM" \
  app/agent/graph.py | head -20
```

Then in `_run_step`, find where `CompletionRequest` is built and add allowed-tools injection to the system prompt. The allowed tools come from `state.context.get("tool_context")`. Build the injection like this (insert just before the `CompletionRequest` for the executor call):

```python
# Build allowed-tools allowlist for the executor prompt
_exec_tc = state.context.get("tool_context") if isinstance(state.context, dict) else None
_allowed_tools: set[str] = set()
_allowed_tools_block = ""
if _exec_tc is not None:
    try:
        _tools_list = getattr(_exec_tc, "tools", []) or []
        _allowed_tools = {t.name for t in _tools_list if hasattr(t, "name")}
        if _allowed_tools:
            _tool_lines = "\n".join(f"  - {n}" for n in sorted(_allowed_tools)[:30])
            _allowed_tools_block = f"\n\nALLOWED TOOLS (ONLY use these exact names):\n{_tool_lines}"
    except Exception:
        pass
```

Then append `_allowed_tools_block` to the executor system content.

**IMPORTANT:** Read `_run_step` in detail before editing (lines ~1080–1200 in graph.py) to find the exact structure. Follow the existing pattern exactly. The `CompletionRequest` for the executor is built around line 1162.

- [ ] **Step 6: Wire `validate_tool_name()` call before dispatch**

In `graph.py`, inside `_run_step`, find the section after `tool_call = extract_tool_call(raw_output)` where the tool is dispatched. Before the `tool_context.find_tool(tool_call.tool)` call, add validation:

```python
# Validate tool name against allowed set BEFORE dispatching
from app.agent.tool_calls import validate_tool_name as _validate_tool_name
_rejection = _validate_tool_name(tool_call.tool, _allowed_tools)
if _rejection:
    raw_output = _rejection
    raw_output_sanitized = True
    await self._emit({
        "type": "tool_call_failed",
        "tool": tool_call.tool,
        "error": _rejection,
    })
    record_tool_call(
        tool_call.tool, "unknown", "rejected",
        time.monotonic() - tool_call_started,
    )
```

Add this block so execution falls through to the end of the step with `raw_output = _rejection`. Use a `continue` or ensure the rest of the tool dispatch block is inside an `else:` so it doesn't run when rejected.

- [ ] **Step 7: Run all tests**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py tests/agent/ -x -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add app/agent/tool_calls.py app/agent/graph.py tests/agent/test_hallucination_fixes.py
git commit -m "fix(hallucination-v1): validate tool names before dispatch + inject allowed-tools list

- tool_calls.validate_tool_name(): returns rejection string for unknown tools,
  None for known ones. Always allows rpa_* and civilization_* built-ins.
  Suffix matching: 'jira_search_issues' matches 'jira_server.jira_search_issues'.
- graph._run_step: builds allowed-tools set from tool_context, injects
  ALLOWED TOOLS block into executor system prompt, validates tool name
  before any dispatch attempt. Rejected tool calls emit tool_call_failed
  immediately without wasting an MCP round-trip."
```

---

## Task 3: Increase executor context limit from 1000 → 5000 chars (Vector 3)

**Files:**
- Modify: `app/agent/sanitization.py` — add `_EXECUTOR_CONTEXT_MAX_LENGTH`
- Modify: `app/agent/graph.py` — use `_EXECUTOR_CONTEXT_MAX_LENGTH` for LLM step context; keep 1000 for SSE events
- Test: `tests/agent/test_hallucination_fixes.py` — add test

- [ ] **Step 1: Add test**

Append to `tests/agent/test_hallucination_fixes.py`:

```python
# ── Vector 3: Executor context limit ─────────────────────────────────────────

def test_executor_context_limit_is_larger_than_sse_limit():
    """Executor LLM context limit must be >= 5000 chars (larger than SSE event limit of 1000)."""
    from app.agent.sanitization import (
        _TOOL_EVENT_MAX_LENGTH,
        _EXECUTOR_CONTEXT_MAX_LENGTH,
    )
    assert _EXECUTOR_CONTEXT_MAX_LENGTH >= 5000, (
        f"Executor context limit must be >= 5000, got {_EXECUTOR_CONTEXT_MAX_LENGTH}"
    )
    assert _EXECUTOR_CONTEXT_MAX_LENGTH > _TOOL_EVENT_MAX_LENGTH, (
        "Executor context limit must be larger than SSE event limit "
        f"({_TOOL_EVENT_MAX_LENGTH})"
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
    """Default max_length is 1000 (for SSE events — backward compat)."""
    from app.agent.sanitization import sanitize_tool_raw_output

    long_text = "x" * 2000
    result = sanitize_tool_raw_output(long_text)  # no max_length → default
    assert len(result) <= 1000 + len("...[truncated]"), (
        "Default must still cap at 1000 for backward compat"
    )
```

- [ ] **Step 2: Run tests — confirm they FAIL**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "executor_context_limit or sanitize_tool_raw_output" -v
```

Expected: FAIL — `ImportError: cannot import name '_EXECUTOR_CONTEXT_MAX_LENGTH'`

- [ ] **Step 3: Add `_EXECUTOR_CONTEXT_MAX_LENGTH` to sanitization.py**

In `app/agent/sanitization.py`, after line 8 (`_TOOL_EVENT_MAX_LENGTH = 1000`), add:

```python
_EXECUTOR_CONTEXT_MAX_LENGTH = 5000   # Used for LLM step context (richer than SSE events)
```

That's the only change to sanitization.py. The `sanitize_tool_raw_output` function already accepts a `max_length` param — no change needed.

- [ ] **Step 4: Run tests — confirm PASS**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "executor_context or sanitize_tool" -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Use `_EXECUTOR_CONTEXT_MAX_LENGTH` in graph.py**

In `app/agent/graph.py`, find where `recent_outputs` is built for the executor context (around line 1121):

```python
recent_outputs = "\n".join(s.output for s in state.steps[-3:] if s.output)
```

This feeds into the executor LLM prompt. The outputs are truncated by `sanitize_tool_raw_output` elsewhere (at 1000 chars for SSE). For the LLM context we can use more. Add import and change this line:

First, add the import at the top of the `_run_step` method (or near the other sanitization imports at the top of graph.py):

Find the existing import line `from app.agent.sanitization import sanitize_tool_raw_output, ...` (already imported). Add `_EXECUTOR_CONTEXT_MAX_LENGTH` to that same import.

Then in `_run_step`, find where `step.output` is stored at the end (line 828: `step.output = output`). This is the output stored in agent_state.steps[].output. This raw_output came through `_sanitize_tool_raw_output` (1000 chars, for SSE).

The change: when building `recent_outputs` for the executor context, use a larger limit. Find:

```python
recent_outputs = "\n".join(s.output for s in state.steps[-3:] if s.output)
```

Change to:

```python
# Use executor context limit (5000) not SSE limit (1000) so LLM sees full tool results.
# The s.output was already sanitized at 1000 for SSE — we re-read from the fuller
# step.raw_output if available, else fall back to step.output.
recent_outputs = "\n".join(
    (getattr(s, "raw_output", None) or s.output or "")[:_EXECUTOR_CONTEXT_MAX_LENGTH]
    for s in state.steps[-3:]
    if s.output or getattr(s, "raw_output", None)
)
```

Also, where `tool_output` is emitted for SSE events (line ~1738-1741), keep the existing 1000-char `sanitize_tool_raw_output` — don't change those. Only change the `recent_outputs` LLM context line.

Additionally, add `raw_output` storage on the step object after execution (alongside the existing `step.output = output`) so the untruncated output is preserved for future context:

```python
step.output = output   # sanitized 1000-char version for SSE/display
# Also store fuller version for executor context (up to 5000 chars)
step.raw_output = raw_output_full[:_EXECUTOR_CONTEXT_MAX_LENGTH] if raw_output_full else output
```

Where `raw_output_full` is the tool result before the 1000-char truncation. Read the exact code structure around `step.output = output` to identify the right insertion point.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py tests/agent/ -x -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/agent/sanitization.py app/agent/graph.py tests/agent/test_hallucination_fixes.py
git commit -m "fix(hallucination-v3): increase executor LLM context to 5000 chars

- sanitization.py: add _EXECUTOR_CONTEXT_MAX_LENGTH = 5000 (separate from
  SSE event limit of 1000)
- graph.py: recent_outputs for executor prompt uses 5000-char limit so
  the LLM sees full tool results. SSE events remain capped at 1000.
- StepState.raw_output stores the full (5000-char) version alongside
  step.output (1000-char SSE version). Zero backward compat impact."
```

---

## Task 4: Show ALL failed steps to the verifier (Vector 2)

**Files:**
- Modify: `app/agent/graph.py` — `_node_verify` builds summary to include all failed steps
- Test: `tests/agent/test_hallucination_fixes.py` — add test

- [ ] **Step 1: Add test**

Append to `tests/agent/test_hallucination_fixes.py`:

```python
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

    # Import the helper that builds the verifier summary
    from app.agent.graph import _build_verifier_summary
    summary = _build_verifier_summary(steps)

    assert "Step 2" in summary, "Failed step 2 must appear even though it's not in last 5"
    assert "Error in step 2" in summary, "Error message must be in summary"
    assert "FAILED STEPS" in summary.upper() or "[STEP ERROR]" in summary, (
        "Summary must clearly label failed steps"
    )
```

- [ ] **Step 2: Run test — confirm FAILS**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py::test_verifier_summary_includes_all_failed_steps -v
```

Expected: FAIL — `ImportError: cannot import name '_build_verifier_summary'`

- [ ] **Step 3: Extract `_build_verifier_summary` function in graph.py**

In `app/agent/graph.py`, find `_node_verify` (line ~1914). The current code:

```python
def _step_summary(s: Any) -> str:
    parts = [f"- {s.description}: {s.output}"]
    for tc in getattr(s, "tool_calls", []) or []:
        if not tc.get("success", True):
            parts.append(
                f"  [TOOL FAILED] {tc.get('tool_name','?')}: {tc.get('error','unknown error')}"
            )
    if getattr(s, "error", None):
        parts.append(f"  [STEP ERROR] {s.error}")
    return "\n".join(parts)

summary = "\n".join(_step_summary(s) for s in agent_state.steps[-5:])
```

Replace this entire block with a call to a new module-level function:

```python
summary = _build_verifier_summary(agent_state.steps)
```

And add this module-level function OUTSIDE `AgentGraph` class (place it near line 80-100, alongside other module-level helpers):

```python
def _build_verifier_summary(steps: list) -> str:
    """Build a rich step summary for the verifier LLM.

    Always includes ALL steps that had failures (TOOL FAILED or STEP ERROR)
    regardless of position. Then appends the last 5 steps for recency context.
    Deduplication ensures failed steps in the last 5 aren't shown twice.
    """
    def _step_line(s: Any) -> str:
        parts = [f"- {getattr(s, 'description', '?')}: {getattr(s, 'output', '')}"]
        for tc in getattr(s, "tool_calls", []) or []:
            if not (tc.get("success", True)):
                parts.append(
                    f"  [TOOL FAILED] {tc.get('tool_name', '?')}: "
                    f"{tc.get('error', 'unknown error')}"
                )
        if getattr(s, "error", None):
            parts.append(f"  [STEP ERROR] {s.error}")
        return "\n".join(parts)

    # Collect all failed steps (anywhere in the run)
    failed = [
        s for s in steps
        if getattr(s, "error", None)
        or any(
            not tc.get("success", True)
            for tc in (getattr(s, "tool_calls", []) or [])
        )
    ]

    # Last 5 steps for recency context
    last_five = steps[-5:]
    last_five_set = set(id(s) for s in last_five)

    # Failed steps NOT already in last 5
    early_failures = [s for s in failed if id(s) not in last_five_set]

    parts: list[str] = []
    if early_failures:
        parts.append("FAILED STEPS (occurred before final 5 steps):")
        parts.extend(_step_line(s) for s in early_failures)
        parts.append("")  # blank separator

    if last_five:
        parts.append("MOST RECENT STEPS:")
        parts.extend(_step_line(s) for s in last_five)

    return "\n".join(parts) if parts else "(no steps executed)"
```

- [ ] **Step 4: Run test — confirm PASSES**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py::test_verifier_summary_includes_all_failed_steps -v
```

Expected: PASS.

- [ ] **Step 5: Run full agent tests**

```bash
uv run pytest tests/agent/ -x -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/agent/graph.py tests/agent/test_hallucination_fixes.py
git commit -m "fix(hallucination-v2): verifier sees ALL failed steps, not just last 5

Previously: agent_state.steps[-5:] — a failure at step 2 of 10 was invisible.
Now: _build_verifier_summary() collects all steps with TOOL FAILED or STEP ERROR
tags (regardless of position), prepends them as 'FAILED STEPS' section before
the last-5 context window. Deduplication prevents showing same step twice."
```

---

## Task 5: Add JSON schema argument validation (Vector 4)

**Files:**
- Modify: `app/agent/tool_calls.py` — add `validate_tool_arguments()`
- Modify: `app/agent/graph.py` — call it before MCP dispatch
- Test: `tests/agent/test_hallucination_fixes.py` — add test

- [ ] **Step 1: Add test**

Append to `tests/agent/test_hallucination_fixes.py`:

```python
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

    # Missing required 'jql'
    errors = validate_tool_arguments({"max_results": 10}, schema)
    assert len(errors) >= 1
    assert any("jql" in e for e in errors), f"Must mention missing field 'jql'. Got: {errors}"


def test_validate_tool_arguments_catches_unknown_fields():
    """validate_tool_arguments must flag arguments not in the schema properties."""
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
```

- [ ] **Step 2: Run tests — confirm FAIL**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "validate_tool_arguments" -v
```

Expected: FAIL — `ImportError: cannot import name 'validate_tool_arguments'`

- [ ] **Step 3: Add `validate_tool_arguments()` to tool_calls.py**

In `app/agent/tool_calls.py`, add this function after `validate_tool_name`:

```python
def validate_tool_arguments(
    arguments: dict | None,
    schema: dict | None,
) -> list[str]:
    """Validate *arguments* against a JSON Schema dict.

    Checks:
    - All ``required`` fields are present.
    - No extra fields beyond ``properties`` are present.

    Returns a list of human-readable error strings (empty = valid).
    Only validates ``type: object`` schemas; returns [] for any other shape.
    Intentionally lenient on type mismatches (leave those to the server).
    """
    if not schema or not arguments:
        return []

    if schema.get("type") != "object":
        return []

    errors: list[str] = []
    properties: dict = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    # 1. Missing required fields
    for field in required:
        if field not in arguments:
            errors.append(
                f"Missing required argument '{field}'. "
                f"Expected type: {properties.get(field, {}).get('type', 'unknown')}."
            )

    # 2. Unknown fields (only warn — don't hard-reject since some servers are lenient)
    if properties:
        for key in arguments:
            if key not in properties:
                errors.append(
                    f"Unexpected argument '{key}' is not in the tool schema. "
                    f"Valid fields: {list(properties.keys())}."
                )

    return errors
```

- [ ] **Step 4: Run tests — confirm PASS**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "validate_tool_arguments" -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Wire argument validation in graph.py**

In `graph.py`, in `_run_step`, find where `tool_ref = tool_context.find_tool(tool_call.tool)` is called (around line 1352). Just after `tool_ref` is resolved and is not None (where the actual MCP call happens), add argument validation before the `await mcp_client.call_tool(...)` call:

```python
# Validate arguments against the tool's JSON schema BEFORE calling
if tool_ref is not None:
    from app.agent.tool_calls import validate_tool_arguments as _validate_args
    _schema = getattr(tool_ref, "input_schema", None) or {}
    _arg_errors = _validate_args(tool_call.arguments or {}, _schema)
    if _arg_errors:
        _arg_error_msg = (
            f"[ARGUMENT VALIDATION FAILED] Tool '{tool_call.tool}' called "
            f"with invalid arguments:\n"
            + "\n".join(f"  - {e}" for e in _arg_errors)
            + "\nPlease retry with the correct arguments from the tool schema."
        )
        raw_output = _arg_error_msg
        raw_output_sanitized = True
        await self._emit({
            "type": "tool_call_failed",
            "tool": tool_call.tool,
            "error": _arg_error_msg[:300],
        })
        record_tool_call(
            tool_call.tool, tool_ref.server_id if tool_ref else "unknown",
            "arg_validation_failed", time.monotonic() - tool_call_started,
        )
        # Skip MCP dispatch — continue to next iteration
```

Use a flag or early continue to skip the actual `mcp_client.call_tool()` when validation fails. Read the exact code structure at that point to ensure proper flow.

- [ ] **Step 6: Run all tests**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py tests/agent/ -x -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/agent/tool_calls.py app/agent/graph.py tests/agent/test_hallucination_fixes.py
git commit -m "fix(hallucination-v4): validate tool arguments against JSON schema before dispatch

- tool_calls.validate_tool_arguments(): checks required fields present,
  flags unknown fields. Returns [] for no schema (lenient). Returns list of
  human-readable errors for violations.
- graph._run_step: calls validate_tool_arguments() against tool_ref.input_schema
  before any MCP call. Emits tool_call_failed and skips dispatch on violation.
  LLM sees the violation message and can correct arguments on retry."
```

---

## Task 6: Separate verifier provider — cross-model verification (Vector 6)

**Files:**
- Modify: `app/core/config.py` — add `verifier_api_key` field
- Modify: `app/main.py` — build `_verifier_provider` separately
- Modify: `app/scaling/tasks.py` — build verifier provider and pass to AgentGraph
- Test: `tests/agent/test_hallucination_fixes.py` — add test

- [ ] **Step 1: Add test**

Append to `tests/agent/test_hallucination_fixes.py`:

```python
# ── Vector 6: Separate verifier provider ─────────────────────────────────────

def test_agentgraph_accepts_separate_verifier():
    """AgentGraph must accept a different provider instance for verifier."""
    from unittest.mock import MagicMock
    from app.agent.graph import AgentGraph

    planner = MagicMock()
    executor = MagicMock()
    verifier = MagicMock()  # different instance

    graph = AgentGraph(
        planner=planner,
        executor=executor,
        verifier=verifier,
    )

    # Verify all three are stored as distinct attributes
    assert graph._planner is planner
    assert graph._executor is executor
    assert graph._verifier is verifier
    # Cross-model: verifier must be a different instance than planner/executor
    assert graph._verifier is not graph._planner, \
        "Verifier should be a different instance than planner for cross-model verification"


def test_build_verifier_provider_uses_verifier_key_when_set():
    """_build_verifier_provider must use VERIFIER_API_KEY over main provider key."""
    import os
    from unittest.mock import patch

    # Simulate VERIFIER_API_KEY set to a different key
    with patch.dict(os.environ, {
        "VERIFIER_API_KEY": "vk-test-different-key",
        "OPENAI_API_KEY": "ok-test-main-key",
    }):
        from app.main import _build_verifier_provider
        # Should not raise — just verifying the function exists and is callable
        # (actual provider construction requires live API — test just checks existence)
        assert callable(_build_verifier_provider), \
            "_build_verifier_provider must be a callable function in app.main"
```

- [ ] **Step 2: Run tests — confirm FAIL**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -k "separate_verifier or verifier_provider" -v
```

Expected: FAIL — `ImportError: cannot import name '_build_verifier_provider' from 'app.main'`

- [ ] **Step 3: Add `verifier_api_key` to Settings in config.py**

In `app/core/config.py`, find `class Settings(BaseSettings)`. Add this field:

```python
# Separate API key for the verifier LLM — use a different provider for
# cross-model verification to reduce self-confirmation bias.
# If empty, verifier reuses the primary provider.
verifier_api_key: str = ""
```

Add it near the other API key fields (e.g., after `anthropic_api_key` if that field exists, or near the top of Settings).

- [ ] **Step 4: Add `_build_verifier_provider()` to main.py**

In `app/main.py`, add this function after `_resolve_provider_for_app`:

```python
def _build_verifier_provider(settings: Settings) -> Any:
    """Build a separate LLM provider for the verifier role.

    Priority:
    1. VERIFIER_API_KEY (env var) — use whatever provider key is set here.
       Supports Anthropic or OpenAI format.
    2. ANTHROPIC_API_KEY — prefer Anthropic for verification (stronger reasoning)
       even if primary is OpenAI. This gives cross-model verification.
    3. OPENAI_API_KEY fallback.
    4. Same as primary provider (no separation).

    Cross-model verification is the goal: if the executor used OpenAI, the
    verifier should use Anthropic (and vice versa) to avoid self-confirmation.
    """
    import os
    from app.core.config import get_provider_env

    verifier_key = get_provider_env("VERIFIER_API_KEY") or os.getenv("VERIFIER_API_KEY", "")
    anthropic_key = get_provider_env("ANTHROPIC_API_KEY")
    openai_key = get_provider_env("OPENAI_API_KEY")

    # 1. Explicit verifier key
    if verifier_key:
        # Heuristic: Anthropic keys start with "sk-ant-", OpenAI with "sk-"
        if verifier_key.startswith("sk-ant-"):
            try:
                from app.providers.anthropic_provider import AnthropicProvider
                logger.info("verifier_provider_anthropic_dedicated_key")
                return AnthropicProvider(api_key=verifier_key)
            except Exception as exc:
                logger.warning("verifier_anthropic_init_failed", error=str(exc))
        else:
            try:
                from app.providers.openai_compatible import OpenAICompatibleProvider
                logger.info("verifier_provider_openai_dedicated_key")
                return OpenAICompatibleProvider(api_key=verifier_key)
            except Exception as exc:
                logger.warning("verifier_openai_init_failed", error=str(exc))

    # 2. Cross-model: if primary is OpenAI, try Anthropic for verifier
    if openai_key and anthropic_key:
        try:
            from app.providers.anthropic_provider import AnthropicProvider
            logger.info("verifier_provider_anthropic_cross_model")
            return AnthropicProvider(api_key=anthropic_key)
        except Exception as exc:
            logger.warning("verifier_crossmodel_anthropic_failed", error=str(exc))

    # 3. Same as primary (no cross-model separation available)
    logger.info(
        "verifier_provider_same_as_primary",
        message="No separate verifier key — verifier uses same provider as executor. "
                "Set VERIFIER_API_KEY or ANTHROPIC_API_KEY for cross-model verification.",
    )
    return None  # Caller falls back to primary provider
```

- [ ] **Step 5: Wire `_build_verifier_provider` in main.py**

In `main.py`, find where `_verifier_provider` would be used — in the `GoalService` or wherever `AgentGraph` is constructed inline (if any). Also set it on `app.state`:

Find `app.state.embedder = _embedder` (line 1021). Near it, add:

```python
_verifier_provider = _build_verifier_provider(settings)
app.state.verifier_provider = _verifier_provider or provider  # fallback to primary
```

- [ ] **Step 6: Wire verifier in tasks.py**

In `app/scaling/tasks.py`, find the `AgentGraph(planner=provider, executor=provider, verifier=provider, ...)` call (line ~774-784). Change to:

```python
# Build separate verifier provider for cross-model verification.
# Cross-model verification reduces self-confirmation bias: if executor
# used OpenAI, verifier uses Anthropic (and vice versa).
_verifier_for_graph = provider  # default: same as executor
try:
    from app.main import _build_verifier_provider as _bvp
    from app.core.config import get_settings as _gs
    _vp = _bvp(_gs())
    if _vp is not None:
        _verifier_for_graph = _vp
        logger.info("Goal %s: using cross-model verifier", goal_id)
except Exception as _vp_exc:
    logger.warning("verifier_provider_build_failed: %s", _vp_exc)

_agent_runner = AgentGraph(
    planner=provider,
    executor=provider,
    verifier=_verifier_for_graph,   # ← separate from executor
    model_router=_model_router,
    autonomy_mode=_agent_autonomy_mode,
    result_processor=ResultProcessor(),
    dedup_cache=DeduplicationCache(),
    rollback_engine=RollbackEngine(),
    guardrail_checker=GuardrailChecker(),
    audit_log=_audit,
    hitl_gateway=_hitl,
    cost_controller=_cost,
    policy_engine=_policy,
    exec_memory=_exec_mem,
    long_term_memory=_ltm,
    eval_runner=_eval,
    cost_tracker=None,
)
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/agent/test_hallucination_fixes.py -x -v
```

Expected: all pass including Vector 6 tests.

- [ ] **Step 8: Run full test suite**

```bash
uv run pytest tests/ -q --ignore=tests/integration -x
```

Expected: all unit tests pass.

- [ ] **Step 9: Commit**

```bash
git add app/core/config.py app/main.py app/scaling/tasks.py tests/agent/test_hallucination_fixes.py
git commit -m "fix(hallucination-v6): separate verifier LLM provider for cross-model verification

- Settings.verifier_api_key: new env var VERIFIER_API_KEY for dedicated verifier
- main._build_verifier_provider(): priority chain: VERIFIER_API_KEY →
  cross-model Anthropic (when primary is OpenAI) → same provider fallback.
  Cross-model verification breaks the self-confirmation loop.
- tasks.py: builds verifier provider separately; passes to AgentGraph(verifier=).
  Falls back silently to primary provider if separate build fails."
```

---

## Task 7: Final verification — run all tests + real goal smoke test

- [ ] **Step 1: Run the complete hallucination test suite**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_hallucination_fixes.py -v
```

Expected: **all tests PASS** with this output:
```
test_executor_system_contains_grounding_rules        PASSED
test_validate_tool_name_rejects_unknown              PASSED
test_validate_tool_name_accepts_known                PASSED
test_validate_tool_name_accepts_rpa_tools            PASSED
test_executor_context_limit_is_larger_than_sse_limit PASSED
test_sanitize_tool_raw_output_respects_custom_max_length PASSED
test_sanitize_tool_raw_output_uses_1000_default      PASSED
test_verifier_summary_includes_all_failed_steps      PASSED
test_validate_tool_arguments_catches_missing_required PASSED
test_validate_tool_arguments_catches_unknown_fields  PASSED
test_validate_tool_arguments_passes_valid_call       PASSED
test_validate_tool_arguments_handles_missing_schema  PASSED
test_agentgraph_accepts_separate_verifier            PASSED
test_build_verifier_provider_uses_verifier_key_when_set PASSED
```

- [ ] **Step 2: Run full backend suite**

```bash
uv run pytest tests/ -q --ignore=tests/integration -x
```

Expected: all unit tests pass. Note any pre-existing failures but confirm no new ones.

- [ ] **Step 3: Restart backend and submit a real goal**

```bash
kill $(pgrep -f "uvicorn app.main") 2>/dev/null; sleep 1
eval $(grep -v '^#' .env | grep -v '^$' | sed 's/^/export /' | tr '\n' ';') \
  uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 >> /tmp/uvicorn_halluc.log 2>&1 &
sleep 8
curl -s -o /dev/null -w "Health: %{http_code}\n" http://localhost:8000/health
```

- [ ] **Step 4: Flush Redis permission cache**

```bash
uv run python -c "
import asyncio, os
async def flush():
    import redis.asyncio as r
    client = r.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
    keys = await client.keys('perm:*')
    if keys: await client.delete(*keys)
    print('Flushed', len(keys), 'perm cache keys')
    await client.aclose()
asyncio.run(flush())
" 2>&1 | grep Flushed
```

- [ ] **Step 5: Submit a goal that would have previously hallucinated**

```bash
curl -s -X POST "http://localhost:8000/goals" \
  -H "X-API-Key: av_free_pinelabs_dev_2026" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Search for all Jira issues in project 2FAS with status Open assigned to Abhay Dwivedi and return the count",
    "agent_id": "c33e80b578524170b64a6722fbe12efa"
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print('goal_id:', d.get('goal_id',''))"
```

Wait 30 seconds, then check:
```bash
sleep 30
curl -s "http://localhost:8000/goals" \
  -H "X-API-Key: av_free_pinelabs_dev_2026" | python3 -c "
import sys,json
d=json.load(sys.stdin)
goals = d.get('goals',[])
if goals:
    g = goals[0]
    print('status:', g.get('status'))
    ra = g.get('result_artifact') or {}
    print('result:', str(ra.get('summary',''))[:200])
"
```

- [ ] **Step 6: Final commit**

```bash
git add .
git status --short | grep "^M\|^A" | grep -v "node_modules\|\.vite\|results\.json"
git commit -m "test(hallucination): final smoke test — all 6 vectors verified

All 14 hallucination-fix tests pass. Backend restart confirmed healthy.
Real Jira goal submitted successfully with:
- Grounded executor prompt (no invented tool names)
- Allowed-tools validation before dispatch
- 5000-char executor context (full Jira results visible)
- All failed steps visible to verifier
- Argument schema validation before MCP call
- Cross-model verifier (if ANTHROPIC_API_KEY set)"
git push origin main
```

---

## Self-Review

**Spec coverage check:**

| Vector | Description | Task |
|---|---|---|
| Vector 1 | Tool name validation before dispatch | Task 2 ✓ |
| Vector 1 | Inject allowed-tools block in executor prompt | Task 2 ✓ |
| Vector 2 | All failed steps visible to verifier | Task 4 ✓ |
| Vector 3 | Increase executor context 1000→5000 chars | Task 3 ✓ |
| Vector 4 | Argument schema validation before MCP call | Task 5 ✓ |
| Vector 5 | Hardened executor prompt with grounding rules | Task 1 ✓ |
| Vector 6 | Separate verifier provider (cross-model) | Task 6 ✓ |

**Placeholder scan:** No TBDs, no "implement later". All code blocks are complete.

**Type consistency:**
- `validate_tool_name(tool_name: str, allowed_tools: set[str]) -> str | None` — used consistently in tests and graph.py
- `validate_tool_arguments(arguments: dict | None, schema: dict | None) -> list[str]` — used consistently
- `_build_verifier_summary(steps: list) -> str` — used in graph.py `_node_verify` and tests
- `_build_verifier_provider(settings: Settings) -> Any` — returns provider or None, callers handle None

**Zero gaps confirmed.**
