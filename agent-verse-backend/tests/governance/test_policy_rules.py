"""Test declarative policy-as-code rule evaluation."""

from app.governance.policy_rules import evaluate_rule, evaluate_rules


def test_deny_external_email():
    rule = {
        "name": "block-external-email",
        "conditions": [
            {"field": "tool_name", "op": "contains", "value": "send_email"},
            {"field": "arguments.to", "op": "not_ends_with", "value": "@company.com"},
        ],
        "logic": "AND",
        "action": "deny",
        "message": "Only @company.com emails allowed",
    }
    ctx = {"tool_name": "send_email", "arguments": {"to": "attacker@evil.com"}}
    result = evaluate_rule(rule, ctx)
    assert result.allowed is False
    assert "company.com" in result.message


def test_allow_internal_email():
    rule = {
        "name": "block-external-email",
        "conditions": [
            {"field": "tool_name", "op": "contains", "value": "send_email"},
            {"field": "arguments.to", "op": "not_ends_with", "value": "@company.com"},
        ],
        "logic": "AND",
        "action": "deny",
    }
    ctx = {"tool_name": "send_email", "arguments": {"to": "bob@company.com"}}
    result = evaluate_rule(rule, ctx)
    assert result.allowed is True


def test_or_logic():
    rule = {
        "name": "block-destructive",
        "conditions": [
            {"field": "tool_name", "op": "contains", "value": "delete"},
            {"field": "tool_name", "op": "contains", "value": "destroy"},
        ],
        "logic": "OR",
        "action": "deny",
    }
    assert evaluate_rule(rule, {"tool_name": "delete_user", "arguments": {}}).allowed is False
    assert evaluate_rule(rule, {"tool_name": "create_user", "arguments": {}}).allowed is True


def test_regex_operator():
    rule = {
        "name": "block-aws-keys",
        "conditions": [{"field": "arguments.value", "op": "regex", "value": r"AKIA[A-Z0-9]{16}"}],
        "logic": "AND",
        "action": "deny",
    }
    ctx = {"arguments": {"value": "AKIAIOSFODNN7EXAMPLE"}}
    assert evaluate_rule(rule, ctx).allowed is False


def test_evaluate_rules_first_deny_wins():
    rules = [
        {
            "name": "block-write",
            "conditions": [{"field": "tool_name", "op": "contains", "value": "write"}],
            "logic": "AND",
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"tool_name": "write_file", "arguments": {}}).allowed is False
    assert evaluate_rules(rules, {"tool_name": "search", "arguments": {}}).allowed is True
