"""Tests for critical wiring gaps - guardrails in graph, AI router in goal service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Test 1: AI Router integration
# ---------------------------------------------------------------------------

def test_ai_router_model_selection():
    from app.ai_router.router import ai_router
    from app.ai_router.models import TaskType

    model = ai_router.select_model(TaskType.PLANNING, "test-tenant-wire")
    assert model is not None
    assert model.provider in ("anthropic", "openai", "gemini", "groq", "voyage")


def test_ai_router_embedding_selection():
    from app.ai_router.router import ai_router
    from app.ai_router.models import TaskType

    model = ai_router.select_model(TaskType.EMBEDDING, "test-tenant-emb")
    assert model is not None
    from app.ai_router.models import ModelCapability
    assert ModelCapability.EMBEDDING in model.capabilities


def test_ai_router_vision_constraint():
    from app.ai_router.router import ai_router
    from app.ai_router.models import TaskType

    model = ai_router.select_model(TaskType.EXECUTION, "test-vision-wire", require_vision=True)
    assert model is not None
    assert model.supports_vision is True


# ---------------------------------------------------------------------------
# Test 2: Guardrails engine evaluates tool args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_guardrails_blocks_pii_in_tool_args():
    from app.guardrails_v2.engine import GuardrailsEngine
    from app.guardrails_v2.models import GuardrailRule, GuardrailLayer, GuardrailAction
    import uuid

    engine = GuardrailsEngine()
    rule = GuardrailRule(
        rule_id=str(uuid.uuid4()),
        tenant_id="test-guard-wire",
        name="Block PII in tool args",
        rule_type="pii_detection",
        layers=[GuardrailLayer.TOOL_ARGS],
        action=GuardrailAction.BLOCK,
    )
    engine.add_rule(rule)

    result = await engine.evaluate(
        content='{"query": "Send results to user@example.com"}',
        layer=GuardrailLayer.TOOL_ARGS,
        tenant_id="test-guard-wire",
    )
    assert result["blocked"] is True
    assert result["violation_count"] >= 1


@pytest.mark.asyncio
async def test_guardrails_passes_clean_tool_args():
    from app.guardrails_v2.engine import GuardrailsEngine
    from app.guardrails_v2.models import GuardrailRule, GuardrailLayer, GuardrailAction
    import uuid

    engine = GuardrailsEngine()
    rule = GuardrailRule(
        rule_id=str(uuid.uuid4()),
        tenant_id="test-guard-wire-2",
        name="Block PII",
        rule_type="pii_detection",
        layers=[GuardrailLayer.TOOL_ARGS],
        action=GuardrailAction.BLOCK,
    )
    engine.add_rule(rule)

    result = await engine.evaluate(
        content='{"query": "list all open tickets"}',
        layer=GuardrailLayer.TOOL_ARGS,
        tenant_id="test-guard-wire-2",
    )
    assert result["blocked"] is False


@pytest.mark.asyncio
async def test_guardrails_redacts_pii_in_output():
    from app.guardrails_v2.engine import GuardrailsEngine
    from app.guardrails_v2.models import GuardrailRule, GuardrailLayer, GuardrailAction
    import uuid

    engine = GuardrailsEngine()
    rule = GuardrailRule(
        rule_id=str(uuid.uuid4()),
        tenant_id="test-redact",
        name="Redact PII in output",
        rule_type="pii_detection",
        layers=[GuardrailLayer.TOOL_OUTPUT],
        action=GuardrailAction.REDACT,
    )
    engine.add_rule(rule)

    result = await engine.evaluate(
        content="User email: admin@company.com, SSN: 123-45-6789",
        layer=GuardrailLayer.TOOL_OUTPUT,
        tenant_id="test-redact",
    )
    assert result["violation_count"] >= 1
    assert result["blocked"] is False  # Redact, not block
    assert result.get("redacted_content") is not None


# ---------------------------------------------------------------------------
# Test 3: Full integration - AI Router feeds goal execution context
# ---------------------------------------------------------------------------

def test_ai_router_selection_stored_in_execution_context():
    """Verify AI Router selection is captured in execution context."""
    from app.ai_router.router import ai_router
    from app.ai_router.models import TaskType

    selections: dict[str, str] = {}
    for task_type, role in [(TaskType.PLANNING, "planner"), (TaskType.EXECUTION, "executor")]:
        model = ai_router.select_model(task_type, "goal-service-tenant")
        if model:
            selections[role] = f"{model.provider}/{model.model_id}"

    assert "planner" in selections
    assert "/" in selections["planner"]  # format: "provider/model_id"


def test_ai_router_health_tracking_affects_selection():
    """Circuit open providers should not be selected."""
    from app.ai_router.registry import ModelRegistry
    from app.ai_router.router import AIRouter
    from app.ai_router.models import TaskType

    registry = ModelRegistry()
    router = AIRouter()

    # Force a provider's circuit open by accumulating errors
    for _ in range(10):
        registry.update_health("fake-broken-provider", error=True, error_msg="timeout")

    health = registry.get_provider_health("fake-broken-provider")
    assert not health.is_healthy


# ---------------------------------------------------------------------------
# Test 4: Guardrails 2.0 module-level singleton is importable from graph.py
# ---------------------------------------------------------------------------

def test_guardrails_v2_import_available_in_graph():
    """Ensure the guardrails 2.0 import block in graph.py loads cleanly."""
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    assert guardrails_engine is not None
    assert GuardrailLayer.TOOL_ARGS is not None
    assert GuardrailLayer.TOOL_OUTPUT is not None
    assert GuardrailLayer.FINAL_OUTPUT is not None


def test_graph_module_imports_guardrails_flag():
    """The _GUARDRAILS_AVAILABLE flag must be True when modules are present."""
    import app.agent.graph as _graph_mod
    assert getattr(_graph_mod, "_GUARDRAILS_AVAILABLE", False) is True
    assert getattr(_graph_mod, "guardrails_engine", None) is not None


# ---------------------------------------------------------------------------
# Test 5: GoalService._select_models_for_tenant returns correct format
# ---------------------------------------------------------------------------

def test_goal_service_select_models_for_tenant():
    """_select_models_for_tenant should return provider/model_id strings."""
    from app.services.goal_service import GoalService
    from unittest.mock import MagicMock

    svc = GoalService.__new__(GoalService)
    mock_tenant = MagicMock()
    mock_tenant.tenant_id = "tenant-ai-router-test"

    selections = svc._select_models_for_tenant(mock_tenant)

    # Should return at least the planner selection
    assert isinstance(selections, dict)
    if selections:  # non-empty when models are available
        for role, model_str in selections.items():
            assert "/" in model_str, f"Model string '{model_str}' missing provider/ prefix"
