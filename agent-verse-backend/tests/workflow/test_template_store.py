"""Tests for SystemTemplateStore — 25 templates, fork, search."""
from __future__ import annotations

import pytest

from app.workflow.template_store import SystemTemplateStore, TemplateNotFoundError


@pytest.fixture
def store() -> SystemTemplateStore:
    return SystemTemplateStore()


def test_load_all_templates(store: SystemTemplateStore) -> None:
    slugs = store.all_slugs()
    assert len(slugs) == 26, f"Expected 26 templates, got {len(slugs)}: {slugs}"


def test_each_template_has_required_fields(store: SystemTemplateStore) -> None:
    for slug in store.all_slugs():
        t = store.get(slug)
        assert t.slug, f"{slug}: missing slug"
        assert t.name, f"{slug}: missing name"
        assert t.category, f"{slug}: missing category"
        assert t.description, f"{slug}: missing description"


def test_each_template_has_at_least_one_step(store: SystemTemplateStore) -> None:
    for slug in store.all_slugs():
        t = store.get(slug)
        assert len(t.definition.steps) >= 1, f"{slug}: no steps defined"


def test_get_known_template(store: SystemTemplateStore) -> None:
    t = store.get("kyc-automation")
    assert t.slug == "kyc-automation"
    assert t.category == "Financial Services"


def test_get_nonexistent_raises(store: SystemTemplateStore) -> None:
    with pytest.raises(TemplateNotFoundError):
        store.get("this-does-not-exist")


def test_list_no_filter_returns_all(store: SystemTemplateStore) -> None:
    items, total = store.list()
    assert total == 26
    assert len(items) <= 20  # default per_page


def test_list_by_category(store: SystemTemplateStore) -> None:
    items, total = store.list(category="Financial Services")
    assert total >= 3  # kyc, merchant-onboarding, aml, loan, reconciliation
    assert all(t.category == "Financial Services" for t in items)


def test_list_search(store: SystemTemplateStore) -> None:
    items, total = store.list(q="kyc")
    assert total >= 1
    slugs = [t.slug for t in items]
    assert "kyc-automation" in slugs


def test_list_search_tag(store: SystemTemplateStore) -> None:
    items, total = store.list(q="compliance")
    assert total >= 1


def test_categories_returns_all(store: SystemTemplateStore) -> None:
    cats = store.categories()
    assert len(cats) >= 5
    all_cats = {c["category"] for c in cats}
    assert "Financial Services" in all_cats


def test_categories_counts_correct(store: SystemTemplateStore) -> None:
    cats = store.categories()
    total_in_cats = sum(c["count"] for c in cats)
    assert total_in_cats == 26


def test_fork_creates_new_id(store: SystemTemplateStore) -> None:
    original = store.get("kyc-automation")
    forked = store.fork("kyc-automation", tenant_id="tenant-abc")
    assert forked.id != original.definition.id


def test_fork_preserves_steps(store: SystemTemplateStore) -> None:
    original = store.get("kyc-automation")
    forked = store.fork("kyc-automation", tenant_id="tenant-abc")
    assert len(forked.steps) == len(original.definition.steps)


def test_fork_nonexistent_raises(store: SystemTemplateStore) -> None:
    with pytest.raises(TemplateNotFoundError):
        store.fork("nonexistent", "tenant-x")


def test_reload_clears_cache(store: SystemTemplateStore) -> None:
    store.list()  # populate cache
    assert store._cache is not None
    store.reload()
    assert store._cache is None
    # Re-load works
    items, total = store.list()
    assert total == 26


def test_template_tags_are_list(store: SystemTemplateStore) -> None:
    for slug in store.all_slugs():
        t = store.get(slug)
        assert isinstance(t.tags, list), f"{slug}: tags should be a list"


def test_template_to_dict_roundtrip(store: SystemTemplateStore) -> None:
    t = store.get("sre-incident-response")
    d = t.to_dict()
    assert d["slug"] == "sre-incident-response"
    assert "definition" in d
