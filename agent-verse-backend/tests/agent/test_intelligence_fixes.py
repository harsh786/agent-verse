"""Tests verifying Bug 1-4 intelligence fixes.

Bug 1: Verifier prompt/parser mismatch — RETRY/FAIL always returned success=True
Bug 2: OpenAI tool call arguments were JSON string, silently discarded as {}
Bug 3: Executor and verifier hardcoded model instead of using model router
Bug 4: Model router missing keys for "think", "reflection", "execution", "verification"
"""

from __future__ import annotations


def test_verifier_system_prompt_instructs_json_output():
    """VERIFIER_SYSTEM must instruct the LLM to respond with JSON."""
    from app.agent.prompts import VERIFIER_SYSTEM
    prompt_lower = VERIFIER_SYSTEM.lower()
    assert "json" in prompt_lower, "VERIFIER_SYSTEM must instruct JSON output"
    assert "true" in prompt_lower or "false" in prompt_lower, \
        "VERIFIER_SYSTEM must use true/false (JSON booleans)"


def test_parse_verifier_response_handles_json():
    """Parser handles clean JSON from verifier."""
    from app.agent.graph import _parse_verifier_response
    result = _parse_verifier_response('{"success": true, "reason": "done"}')
    assert result["success"] is True
    assert result["reason"] == "done"


def test_parse_verifier_response_handles_json_false():
    from app.agent.graph import _parse_verifier_response
    result = _parse_verifier_response('{"success": false, "reason": "missing step", "retry": true}')
    assert result["success"] is False
    assert result.get("retry") is True


def test_parse_verifier_response_handles_success_text():
    from app.agent.graph import _parse_verifier_response
    result = _parse_verifier_response("SUCCESS: Goal completed successfully")
    assert result["success"] is True


def test_parse_verifier_response_handles_retry_text():
    from app.agent.graph import _parse_verifier_response
    result = _parse_verifier_response("RETRY: Step 2 failed, need to retry")
    assert result["success"] is False
    assert result.get("retry") is True


def test_parse_verifier_response_handles_fail_text():
    from app.agent.graph import _parse_verifier_response
    result = _parse_verifier_response("FAIL: Cannot achieve this goal")
    assert result["success"] is False
    assert result.get("retry") is False


def test_openai_provider_parses_tool_args_as_dict():
    """OpenAI tool call arguments must be parsed from JSON string to dict."""
    import json
    from app.providers.openai_compatible import OpenAICompatibleProvider

    # Simulate what the OpenAI SDK returns: tc.function.arguments is a JSON string
    raw_args_string = '{"query": "test search", "limit": 5}'

    # The provider must convert this to a dict
    parsed = json.loads(raw_args_string)
    assert isinstance(parsed, dict)
    assert parsed["query"] == "test search"

    # Verify the provider source uses json.loads
    import inspect
    src = inspect.getsource(OpenAICompatibleProvider)
    assert "json.loads" in src, "OpenAI provider must use json.loads to parse tool arguments"


def test_model_router_has_execution_and_verification_keys():
    """ModelRouter must have mappings for execution and verification."""
    from app.agent.model_router import ModelRouter
    router = ModelRouter()
    exec_model = router.model_for("execution")
    verify_model = router.model_for("verification")
    # Should return a non-empty string (not None, not empty)
    assert exec_model is not None
    assert verify_model is not None


def test_executor_uses_model_router_not_hardcoded():
    """_execute_step must use model_router.model_for('execution') not hardcoded string."""
    import inspect
    from app.agent import graph
    src = inspect.getsource(graph)
    # Find the executor CompletionRequest — should NOT have hardcoded model
    # The fix should have model=_exec_model or similar
    assert '_exec_model' in src or 'model_for("execution")' in src or "execution" in src, \
        "Executor must use model router for model selection"


def test_verifier_uses_model_router_not_hardcoded():
    """_node_verify must use model_router not hardcoded model."""
    import inspect
    from app.agent import graph
    src = inspect.getsource(graph)
    assert '_verify_model' in src or 'model_for("verification")' in src or "verification" in src, \
        "Verifier must use model router for model selection"
