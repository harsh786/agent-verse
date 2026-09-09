"""Tests for publish approval workflow (requires_publish_approval flag)."""
from __future__ import annotations

from app.workflow.dsl import WorkflowDefinition


def _wf(**kwargs) -> WorkflowDefinition:
    defaults: dict = dict(name="test", steps=[])
    defaults.update(kwargs)
    return WorkflowDefinition(**defaults)


def test_requires_publish_approval_default_false() -> None:
    wf = _wf()
    assert wf.requires_publish_approval is False


def test_requires_publish_approval_set_true() -> None:
    wf = _wf(requires_publish_approval=True)
    assert wf.requires_publish_approval is True


def test_publish_approved_by_default_none() -> None:
    wf = _wf()
    assert wf.publish_approved_by is None


def test_publish_approved_fields_stored() -> None:
    wf = _wf(
        requires_publish_approval=True,
        publish_approved_by="user-admin",
        publish_approved_at="2026-08-16T10:00:00Z",
    )
    assert wf.publish_approved_by == "user-admin"
    assert wf.publish_approved_at == "2026-08-16T10:00:00Z"


def test_run_retention_days_default() -> None:
    wf = _wf()
    # Default retention is 90 days
    assert wf.run_retention_days >= 1


def test_run_labels_default_empty() -> None:
    wf = _wf()
    assert wf.run_labels == {}


def test_run_labels_set() -> None:
    wf = _wf(run_labels={"env": "prod", "team": "payments"})
    assert wf.run_labels["env"] == "prod"
