"""Tests for AgentManifest SDK class."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from app.sdk.manifest import AgentManifest, ConnectorRequirement, PolicySpec


# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_DICT = {
    "name": "test-agent",
    "version": "1.2.3",
    "description": "A test agent",
    "autonomy_mode": "supervised",
    "goal_template": "Run a check on {target}",
    "default_model": "gpt-4o",
    "connector_requirements": [
        {"type": "github", "optional": False, "description": "GitHub access"},
    ],
    "knowledge_collections": ["kb-1", "kb-2"],
    "policies": [
        {"name": "no-delete", "tools_pattern": "*delete*", "action": "deny"},
    ],
    "eval_suite_id": "suite-abc",
    "tags": ["production", "infra"],
}


# ── Test 1: from_dict valid manifest ─────────────────────────────────────────

def test_from_dict_valid_manifest():
    manifest = AgentManifest._from_dict(VALID_DICT)
    assert manifest.name == "test-agent"
    assert manifest.version == "1.2.3"
    assert manifest.description == "A test agent"
    assert manifest.autonomy_mode == "supervised"
    assert manifest.goal_template == "Run a check on {target}"
    assert manifest.default_model == "gpt-4o"
    assert manifest.eval_suite_id == "suite-abc"


# ── Test 2: validate() returns empty for valid manifest ───────────────────────

def test_validate_returns_empty_for_valid():
    manifest = AgentManifest._from_dict(VALID_DICT)
    errors = manifest.validate()
    assert errors == []


# ── Test 3: validate() catches missing name ───────────────────────────────────

def test_validate_catches_missing_name():
    data = {**VALID_DICT, "name": ""}
    manifest = AgentManifest._from_dict(data)
    errors = manifest.validate()
    assert any("name is required" in e for e in errors)


# ── Test 4: validate() catches invalid autonomy_mode ─────────────────────────

def test_validate_catches_invalid_autonomy_mode():
    data = {**VALID_DICT, "autonomy_mode": "rogue"}
    manifest = AgentManifest._from_dict(data)
    errors = manifest.validate()
    assert any("invalid autonomy_mode" in e for e in errors)
    assert any("rogue" in e for e in errors)


# ── Test 5: validate() catches invalid policy action ─────────────────────────

def test_validate_catches_invalid_policy_action():
    data = {
        **VALID_DICT,
        "policies": [{"name": "bad-policy", "tools_pattern": "*", "action": "allow"}],
    }
    manifest = AgentManifest._from_dict(data)
    errors = manifest.validate()
    assert any("bad-policy" in e for e in errors)
    assert any("allow" in e for e in errors)


# ── Test 6: to_dict round-trips correctly ─────────────────────────────────────

def test_to_dict_round_trip():
    manifest = AgentManifest._from_dict(VALID_DICT)
    d = manifest._to_dict()
    manifest2 = AgentManifest._from_dict(d)
    assert manifest2.name == manifest.name
    assert manifest2.version == manifest.version
    assert manifest2.autonomy_mode == manifest.autonomy_mode
    assert manifest2.eval_suite_id == manifest.eval_suite_id
    assert manifest2.tags == manifest.tags


# ── Test 7: connector_requirements parsed correctly ───────────────────────────

def test_connector_requirements_parsed():
    manifest = AgentManifest._from_dict(VALID_DICT)
    assert len(manifest.connector_requirements) == 1
    req = manifest.connector_requirements[0]
    assert isinstance(req, ConnectorRequirement)
    assert req.type == "github"
    assert req.optional is False
    assert req.description == "GitHub access"


# ── Test 8: policies parsed correctly ────────────────────────────────────────

def test_policies_parsed():
    manifest = AgentManifest._from_dict(VALID_DICT)
    assert len(manifest.policies) == 1
    policy = manifest.policies[0]
    assert isinstance(policy, PolicySpec)
    assert policy.name == "no-delete"
    assert policy.tools_pattern == "*delete*"
    assert policy.action == "deny"


# ── Test 9: from_yaml raises ImportError if pyyaml missing ───────────────────

def test_from_yaml_raises_import_error_without_pyyaml(tmp_path):
    fake_yaml = tmp_path / "agent.yaml"
    fake_yaml.write_text("name: test\nversion: 1.0.0\ndescription: test\n")
    with patch.dict("sys.modules", {"yaml": None}):
        with pytest.raises(ImportError, match="pyyaml is required"):
            AgentManifest.from_yaml(str(fake_yaml))


# ── Test 10: tags field ───────────────────────────────────────────────────────

def test_tags_field():
    manifest = AgentManifest._from_dict(VALID_DICT)
    assert manifest.tags == ["production", "infra"]

    # Empty tags when not provided
    data_no_tags = {**VALID_DICT}
    del data_no_tags["tags"]
    manifest2 = AgentManifest._from_dict(data_no_tags)
    assert manifest2.tags == []
