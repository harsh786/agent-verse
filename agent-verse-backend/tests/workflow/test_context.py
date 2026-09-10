"""Tests for ContextResolver — all {{...}} variable resolution."""
from __future__ import annotations

import pytest

from app.workflow.context import ContextResolver


@pytest.fixture
def resolver():
    return ContextResolver()


@pytest.fixture
def state():
    return {
        "run_id": "run-123",
        "tenant_id": "tenant-456",
        "inputs": {"doc_url": "https://example.com/doc.pdf", "count": 42},
        "step_outputs": {
            "step1": {"result": "hello", "confidence": 0.95, "nested": {"key": "val"}},
        },
        "vars": {"total": 10, "flag": True},
        "raw_trigger": {"data": {"file": "test.pdf"}},
        "_env": {"BASE_URL": "https://api.example.com"},
        "_foreach_ctx": {"item": {"name": "Alice"}, "index": 2, "total": 5},
    }


def test_resolve_inputs(resolver, state):
    assert resolver.resolve("{{inputs.doc_url}}", state) == "https://example.com/doc.pdf"


def test_resolve_inputs_number(resolver, state):
    assert resolver.resolve("{{inputs.count}}", state) == 42


def test_resolve_step_output_field(resolver, state):
    assert resolver.resolve("{{steps.step1.output.result}}", state) == "hello"


def test_resolve_step_output_nested(resolver, state):
    assert resolver.resolve("{{steps.step1.output.nested.key}}", state) == "val"


def test_resolve_step_output_full_dict(resolver, state):
    result = resolver.resolve("{{steps.step1.output}}", state)
    assert result == {"result": "hello", "confidence": 0.95, "nested": {"key": "val"}}


def test_resolve_vars(resolver, state):
    assert resolver.resolve("{{vars.total}}", state) == 10


def test_resolve_vars_bool(resolver, state):
    assert resolver.resolve("{{vars.flag}}", state) is True


def test_resolve_env(resolver, state):
    assert resolver.resolve("{{env.BASE_URL}}", state) == "https://api.example.com"


def test_resolve_foreach_item(resolver, state):
    assert resolver.resolve("{{foreach.item.name}}", state) == "Alice"


def test_resolve_foreach_index(resolver, state):
    assert resolver.resolve("{{foreach.index}}", state) == 2


def test_resolve_foreach_total(resolver, state):
    assert resolver.resolve("{{foreach.total}}", state) == 5


def test_resolve_trigger_field(resolver, state):
    assert resolver.resolve("{{trigger.data.file}}", state) == "test.pdf"


def test_resolve_workflow_run_id(resolver, state):
    assert resolver.resolve("{{workflow.run_id}}", state) == "run-123"


def test_resolve_workflow_tenant_id(resolver, state):
    assert resolver.resolve("{{workflow.tenant_id}}", state) == "tenant-456"


def test_resolve_workflow_now_iso(resolver, state):
    result = resolver.resolve("{{workflow.now_iso}}", state)
    assert "T" in result  # ISO-8601 always has T separator


def test_resolve_mixed_string(resolver, state):
    result = resolver.resolve("Hello {{inputs.doc_url}} world", state)
    assert result == "Hello https://example.com/doc.pdf world"


def test_resolve_missing_path_returns_none(resolver, state):
    assert resolver.resolve("{{steps.nonexistent.output.foo}}", state) is None


def test_resolve_dict(resolver, state):
    d = {"url": "{{inputs.doc_url}}", "static": "unchanged"}
    result = resolver.resolve_dict(d, state)
    assert result["url"] == "https://example.com/doc.pdf"
    assert result["static"] == "unchanged"


def test_vault_placeholder_returned_when_no_vault(resolver, state):
    result = resolver.resolve("{{vault://MY_SECRET}}", state)
    assert "vault:MY_SECRET" in str(result)


def test_vault_key_tracked(resolver, state):
    resolver.reset_vault_tracking()
    resolver.resolve("{{vault://API_KEY}}", state)
    assert "API_KEY" in resolver.vault_keys_used


def test_non_string_passthrough(resolver, state):
    assert resolver.resolve(42, state) == 42
    assert resolver.resolve(None, state) is None
    assert resolver.resolve({"a": 1}, state) == {"a": 1}
