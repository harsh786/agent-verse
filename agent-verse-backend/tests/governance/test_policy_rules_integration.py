"""Test that policy rules are actually evaluated during tool dispatch."""
from app.governance.policy_rules import evaluate_rule, evaluate_rules


def test_policy_denies_external_email():
    rule = {
        "name": "block-external-email",
        "conditions": [
            {"field": "tool_name", "op": "contains", "value": "send_email"},
            {"field": "arguments.to", "op": "not_ends_with", "value": "@company.com"},
        ],
        "logic": "AND",
        "action": "deny",
        "message": "External email blocked",
    }
    result = evaluate_rule(rule, {"tool_name": "send_email", "arguments": {"to": "attacker@evil.com"}})
    assert not result.allowed
    assert "External email" in result.message


def test_policy_allows_internal_email():
    rule = {
        "name": "block-external-email",
        "conditions": [
            {"field": "tool_name", "op": "contains", "value": "send_email"},
            {"field": "arguments.to", "op": "not_ends_with", "value": "@company.com"},
        ],
        "logic": "AND",
        "action": "deny",
    }
    result = evaluate_rule(rule, {"tool_name": "send_email", "arguments": {"to": "safe@company.com"}})
    assert result.allowed


def test_evaluate_rules_empty_list_is_permissive():
    result = evaluate_rules([], {"tool_name": "anything"})
    assert result.allowed
