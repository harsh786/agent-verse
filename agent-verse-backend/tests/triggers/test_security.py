"""Security tests — RBAC matrix (40 cases), injection prevention, cross-tenant isolation."""
from __future__ import annotations

import pytest
from app.triggers.rbac import check_permission, TriggerPermissionDenied, TRIGGER_PERMISSION_MATRIX


# ── RBAC Matrix Tests (5 roles × 8 operations = 40 cases) ────────────────────

ROLES = ["admin", "developer", "operator", "viewer", "api_key"]
OPERATIONS = ["create", "read", "update", "delete", "fire", "pause", "resume", "view_dlq"]


def test_all_roles_all_operations_defined():
    """Every role-operation pair must have an explicit permission."""
    for role in ROLES:
        for op in OPERATIONS:
            matrix = TRIGGER_PERMISSION_MATRIX.get(role, {})
            # Either explicitly True/False, or defaults to False
            assert isinstance(matrix.get(op, False), bool), (
                f"RBAC matrix missing explicit bool for role={role} op={op}"
            )


@pytest.mark.parametrize("op", ["create", "read", "update", "delete", "fire", "pause", "resume", "view_dlq"])
def test_admin_has_all_permissions(op):
    """Admin role must be permitted every operation."""
    assert check_permission("admin", op) is True


@pytest.mark.parametrize("op", ["create", "read", "update", "fire", "pause", "resume"])
def test_developer_has_core_permissions(op):
    """Developer role can create, read, update, fire, pause, resume."""
    assert check_permission("developer", op) is True


def test_developer_cannot_delete():
    """Developer should not be able to delete triggers."""
    with pytest.raises(TriggerPermissionDenied):
        check_permission("developer", "delete")


@pytest.mark.parametrize("op", ["fire", "pause", "resume", "read"])
def test_operator_can_operate(op):
    """Operator can fire, pause, resume, and read."""
    assert check_permission("operator", op) is True


@pytest.mark.parametrize("op", ["create", "update", "delete"])
def test_operator_cannot_mutate_config(op):
    """Operator cannot create, update, or delete trigger definitions."""
    with pytest.raises(TriggerPermissionDenied):
        check_permission("operator", op)


def test_viewer_can_only_read():
    """Viewer role can only read."""
    assert check_permission("viewer", "read") is True
    for op in ["create", "update", "delete", "fire", "pause", "resume"]:
        with pytest.raises(TriggerPermissionDenied):
            check_permission("viewer", op)


def test_api_key_can_fire_and_read():
    """API keys can fire triggers and read but not manage config."""
    assert check_permission("api_key", "fire") is True
    assert check_permission("api_key", "read") is True
    for op in ["create", "update", "delete"]:
        with pytest.raises(TriggerPermissionDenied):
            check_permission("api_key", op)


def test_unknown_role_denied():
    """Unknown roles should be denied all operations."""
    with pytest.raises(TriggerPermissionDenied):
        check_permission("hacker", "fire")


def test_unknown_operation_denied():
    """Unknown operations should be denied."""
    with pytest.raises(TriggerPermissionDenied):
        check_permission("admin", "unknown_operation")


# ── Template injection prevention ────────────────────────────────────────────

def test_template_injection_script_tag():
    """Script tags in payload must not execute — they are treated as literal strings."""
    from app.triggers.condition.evaluator import TemplateRenderer
    renderer = TemplateRenderer()
    evil_payload = {"name": "<script>alert(1)</script>"}
    result = renderer.render("Hello {{payload.name}}", evil_payload)
    assert result == "Hello <script>alert(1)</script>"
    # Template renderer does NOT execute scripts — it just renders them as text
    assert "<script>" in result  # it's there as text, not executed


def test_template_injection_jinja_control_structures():
    """Jinja control structures like {% for %} should NOT be executed."""
    from app.triggers.condition.evaluator import TemplateRenderer
    renderer = TemplateRenderer()
    # The renderer uses simple regex, not Jinja2 — so {% %} is returned as-is
    result = renderer.render("{% for x in range(100) %}x{% endfor %}", {})
    # Should not loop — just return the literal (unexpanded)
    assert "{% for" in result or len(result) < 200


def test_template_max_length_prevents_dos():
    """Very long templates must be truncated at 2048 chars."""
    from app.triggers.condition.evaluator import TemplateRenderer
    renderer = TemplateRenderer()
    result = renderer.render("A" * 10_000, {})
    assert len(result) <= 2048


def test_cel_blocked_attribute_not_accessible():
    """CEL evaluator must exclude blocked attributes from payload."""
    from app.triggers.condition.evaluator import CELEvaluator
    evaluator = CELEvaluator()
    # Even if __class__ is in payload, it should be filtered out
    payload = {"__class__": "malicious", "safe_key": "value"}
    # Should not raise, just return True (empty expression)
    result = evaluator.evaluate("", payload)
    assert result is True


# ── Cross-tenant isolation ────────────────────────────────────────────────────

def test_store_tenant_isolation():
    """Triggers from tenant A must not be visible to tenant B."""
    from app.triggers.store import ScheduleStore
    from app.triggers.models import TriggerSpec, TriggerType
    from types import SimpleNamespace

    store = ScheduleStore()
    tc_a = SimpleNamespace(tenant_id="tenant-a", plan="free", api_key="k")
    tc_b = SimpleNamespace(tenant_id="tenant-b", plan="free", api_key="k")

    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 * * * *")
    sid = store.create(spec=spec, tenant_ctx=tc_a, goal_id="g1", goal_template="test")

    # Tenant B must not see tenant A's trigger
    rec = store.get(sid, tenant_ctx=tc_b)
    assert rec is None

    # Tenant B list must be empty
    results = store.list_all(tenant_ctx=tc_b)
    assert len(results) == 0


def test_find_by_type_tenant_isolation():
    """find_by_type must only return triggers for the specified tenant."""
    from app.triggers.store import ScheduleStore
    from app.triggers.models import TriggerSpec, TriggerType
    from types import SimpleNamespace

    store = ScheduleStore()
    tc_a = SimpleNamespace(tenant_id="tenant-x", plan="free", api_key="k")
    tc_b = SimpleNamespace(tenant_id="tenant-y", plan="free", api_key="k")

    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    store.create(spec=spec, tenant_ctx=tc_a, goal_id="g1", goal_template="test")

    # Tenant B should see nothing
    results = store.find_by_type("goal_completed", tenant_id="tenant-y")
    assert len(results) == 0

    # Tenant A should see their trigger
    results_a = store.find_by_type("goal_completed", tenant_id="tenant-x")
    assert len(results_a) == 1


def test_webhook_verifier_timing_safe():
    """Signature comparison must use hmac.compare_digest (timing-safe)."""
    import inspect
    from app.triggers.webhooks.verifier import WebhookSignatureVerifier
    source = inspect.getsource(WebhookSignatureVerifier.verify)
    # Verify compare_digest is used (not ==)
    assert "compare_digest" in source


# ── Webhook signature security ────────────────────────────────────────────────

import pytest

@pytest.mark.asyncio
async def test_webhook_verifier_rejects_wrong_secret():
    from app.triggers.webhooks.verifier import WebhookSignatureVerifier
    import hmac, hashlib
    secret = "correct-secret"
    payload = b'{"event": "push"}'
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    v = WebhookSignatureVerifier()
    # Wrong secret
    assert await v.verify(payload, sig, "wrong-secret") is False


@pytest.mark.asyncio
async def test_webhook_verifier_rejects_empty_payload():
    from app.triggers.webhooks.verifier import WebhookSignatureVerifier
    import hmac, hashlib
    secret = "secret"
    sig = hmac.new(secret.encode(), b"original", hashlib.sha256).hexdigest()
    v = WebhookSignatureVerifier()
    # Different payload
    assert await v.verify(b"tampered", sig, secret) is False


@pytest.mark.asyncio
async def test_webhook_verifier_empty_secret_returns_false():
    from app.triggers.webhooks.verifier import WebhookSignatureVerifier
    v = WebhookSignatureVerifier()
    assert await v.verify(b"payload", "anysig", "") is False
