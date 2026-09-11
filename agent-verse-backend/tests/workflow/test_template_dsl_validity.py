"""2.W-2 (templates): every shipped system template must be DSL-valid, use only
registered step types, and compile at load time — not only be discovered lazily
on first access. This is the load-time validation gate: a template that names an
unregistered step type (e.g. an org step before 2.W-8) or an unparseable DSL
would previously ship green and only fail when a tenant instantiated it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.registry import StepTypeRegistry
from app.workflow.template_store import SystemTemplateStore

_SLUGS = SystemTemplateStore().all_slugs()


def test_templates_present() -> None:
    assert len(_SLUGS) == 26


@pytest.mark.parametrize("slug", _SLUGS)
def test_template_dsl_valid_and_compiles(slug: str) -> None:
    store = SystemTemplateStore()
    definition = store.get(slug).definition  # constructs WorkflowDefinition (DSL parse)

    assert definition.steps, f"{slug}: no steps"

    # Every step type must be registered, or the workflow is dead on arrival.
    for step in definition.steps:
        assert StepTypeRegistry.is_registered(step.type), (
            f"{slug}: step {step.id!r} uses unregistered type {step.type!r}"
        )

    # The compiler must build a real LangGraph without raising.
    compiled = WorkflowCompiler(context_resolver=MagicMock()).compile(definition)
    assert compiled is not None
