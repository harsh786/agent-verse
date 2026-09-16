"""Tests for entity versioning — app/org/versioning.py"""
from __future__ import annotations

from app.org.versioning import (
    VERSIONED_ENTITIES,
    EntityVersion,
    EntityVersionManager,
    VersioningConfig,
    VersionStrategy,
    entity_version_manager,
)


def test_global_singleton_exists():
    assert isinstance(entity_version_manager, EntityVersionManager)


def test_versioned_entities_cover_expected_types():
    for key in ("agents", "roles", "prompts", "models", "workflows", "policies"):
        assert key in VERSIONED_ENTITIES


def test_entity_version_is_stable_property():
    ev = EntityVersion(
        entity_type="agents",
        entity_id="a1",
        version="1.0.0",
        strategy=VersionStrategy.SEMANTIC,
        content_hash="abc",
        deployed=True,
        deprecated=False,
    )
    assert ev.is_stable is True
    ev.deprecated = True
    assert ev.is_stable is False


# ── create_version ───────────────────────────────────────────────────────────


def test_create_version_semantic_defaults_to_1_0_0():
    mgr = EntityVersionManager()
    ev = mgr.create_version("agents", "agent-1", {"prompt": "hello"})
    assert ev.version == "1.0.0"
    assert ev.strategy == VersionStrategy.SEMANTIC


def test_create_version_semantic_auto_increments_patch():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {"prompt": "v1"})
    second = mgr.create_version("agents", "agent-1", {"prompt": "v2"})
    assert second.version == "1.0.1"


def test_create_version_semantic_uses_hint_when_provided():
    mgr = EntityVersionManager()
    ev = mgr.create_version("agents", "agent-1", {}, version_hint="2.5.0")
    assert ev.version == "2.5.0"


def test_create_version_hash_strategy():
    mgr = EntityVersionManager()
    ev = mgr.create_version("prompts", "prompt-1", {"text": "hi"})
    assert ev.version.startswith("sha256:")
    assert ev.ab_test_enabled is True


def test_create_version_timestamp_strategy():
    mgr = EntityVersionManager()
    ev = mgr.create_version("knowledge_collections", "kc-1", {})
    assert ev.version.isdigit()


def test_create_version_unknown_entity_type_defaults_to_semantic():
    mgr = EntityVersionManager()
    ev = mgr.create_version("mystery_entity", "e1", {})
    assert ev.strategy == VersionStrategy.SEMANTIC
    assert ev.version == "1.0.0"


def test_create_version_semantic_falls_back_when_last_version_unparsable():
    mgr = EntityVersionManager()
    mgr._versions["agent-x"] = [
        EntityVersion(
            entity_type="agents",
            entity_id="agent-x",
            version="not-a-version",
            strategy=VersionStrategy.SEMANTIC,
            content_hash="abc",
        )
    ]
    ev = mgr.create_version("agents", "agent-x", {})
    assert ev.version == "1.0.0"


# ── deploy / deprecate ────────────────────────────────────────────────────────


def test_deploy_marks_version_deployed():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {})
    assert mgr.deploy("agent-1", "1.0.0") is True
    assert mgr.get_current("agent-1").deployed is True


def test_deploy_unknown_version_returns_false():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {})
    assert mgr.deploy("agent-1", "9.9.9") is False


def test_deploy_blocked_when_immutable_after_deploy_and_already_deployed():
    mgr = EntityVersionManager()
    mgr.create_version("roles", "role-1", {}, version_hint="1.0.0")
    assert mgr.deploy("role-1", "1.0.0") is True
    # Roles are immutable_after_deploy=True — redeploying the same version fails.
    assert mgr.deploy("role-1", "1.0.0") is False


def test_deprecate_marks_version_deprecated():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {})
    assert mgr.deprecate("agent-1", "1.0.0") is True
    assert mgr.get_current("agent-1") is None  # deprecated => not stable


def test_deprecate_unknown_version_returns_false():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {})
    assert mgr.deprecate("agent-1", "9.9.9") is False


# ── get_current / list_versions ──────────────────────────────────────────────


def test_get_current_returns_none_when_nothing_deployed():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {})
    assert mgr.get_current("agent-1") is None


def test_get_current_returns_latest_deployed():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {}, version_hint="1.0.0")
    mgr.create_version("agents", "agent-1", {}, version_hint="2.0.0")
    mgr.deploy("agent-1", "1.0.0")
    mgr.deploy("agent-1", "2.0.0")
    current = mgr.get_current("agent-1")
    assert current.version == "2.0.0"


def test_list_versions_returns_all_including_undeployed():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {}, version_hint="1.0.0")
    mgr.create_version("agents", "agent-1", {}, version_hint="1.1.0")
    assert len(mgr.list_versions("agent-1")) == 2


def test_list_versions_unknown_entity_returns_empty():
    mgr = EntityVersionManager()
    assert mgr.list_versions("nope") == []


# ── A/B testing ───────────────────────────────────────────────────────────────


def test_get_ab_variants_only_deployed_and_ab_enabled():
    mgr = EntityVersionManager()
    mgr.create_version("prompts", "prompt-1", {"v": 1})
    variants = mgr.get_ab_variants("prompt-1")
    assert variants == []  # not deployed yet
    version = mgr.list_versions("prompt-1")[0].version
    mgr.deploy("prompt-1", version)
    variants = mgr.get_ab_variants("prompt-1")
    assert len(variants) == 1


def test_select_ab_version_falls_back_to_current_when_no_variants():
    mgr = EntityVersionManager()
    mgr.create_version("agents", "agent-1", {}, version_hint="1.0.0")
    mgr.deploy("agent-1", "1.0.0")
    selected = mgr.select_ab_version("agent-1", request_hash=42)
    assert selected.version == "1.0.0"


def test_select_ab_version_routes_by_bucket():
    mgr = EntityVersionManager()
    v1 = mgr.create_version("prompts", "prompt-2", {"a": 1}, notes="variant-a")
    v1.ab_test_traffic_pct = 0.5
    v2 = mgr.create_version("prompts", "prompt-2", {"a": 2}, notes="variant-b")
    v2.ab_test_traffic_pct = 0.5
    mgr.deploy("prompt-2", v1.version)
    mgr.deploy("prompt-2", v2.version)
    # bucket = (10 % 100) / 100 = 0.10 -> falls in first variant's cumulative range
    selected = mgr.select_ab_version("prompt-2", request_hash=10)
    assert selected is not None


def test_select_ab_version_with_no_versions_at_all_returns_none():
    mgr = EntityVersionManager()
    assert mgr.select_ab_version("never-created", request_hash=1) is None


def test_select_ab_version_falls_through_when_bucket_exceeds_cumulative_traffic():
    """If traffic percentages don't sum to 1.0 and the bucket lands past the
    last variant's cumulative share, selection falls back to get_current()."""
    mgr = EntityVersionManager()
    v1 = mgr.create_version("prompts", "prompt-3", {"a": 1})
    v1.ab_test_traffic_pct = 0.1  # only covers buckets < 0.1
    mgr.deploy("prompt-3", v1.version)
    # bucket = (99 % 100) / 100 = 0.99, which exceeds the only variant's 0.1 share.
    selected = mgr.select_ab_version("prompt-3", request_hash=99)
    assert selected.version == v1.version  # get_current() falls back to same deployed version


def test_versioning_config_defaults():
    cfg = VersioningConfig(entity_type="custom", strategy=VersionStrategy.SEMANTIC)
    assert cfg.history_enabled is True
    assert cfg.immutable_after_deploy is False
    assert cfg.audit_trail is True
