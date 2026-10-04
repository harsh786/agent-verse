"""RV-06 (a04-F066-N1): repository ingestion on the worker screens with tenant rules.

The repository-ingest Celery task (``app.ingestion.repo_tasks``) built its
worker services without binding ``guardrails_engine`` to the tenant's
``PostgresGuardrailRuleRepository`` — only the connector-sync builder in
``app.ingestion.scheduler`` did. In a fresh worker process:

* in production every repo file's RAG_INGEST screen raised
  ``IngestionScreeningUnavailableError`` (the job failed until a DLQ retry
  happened to land after a sync bound the process);
* outside production it screened against the default rules only, ignoring the
  tenant's persisted block rules.

Pins: the repo worker-services build binds the repository on the worker's
session factory (one shared builder with the scheduler), and a repo ingest run
in production mode applies a tenant block rule and admits a clean file.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.guardrails_v2.engine import guardrails_engine
from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, GuardrailRule
from app.providers.embedder_factory import EmbedderResolution

_TENANT = "rv06-repo-tenant"
_SECRET_WORD = "projectnightingale"


def _tenant_rule() -> GuardrailRule:
    return GuardrailRule(
        rule_id=f"custom:{_TENANT}:codename",
        tenant_id=_TENANT,
        name="block codename in RAG ingest",
        rule_type="keyword_block",
        layers=[GuardrailLayer.RAG_INGEST],
        action=GuardrailAction.BLOCK,
        config={"keywords": [_SECRET_WORD]},
    )


class _FakeRepo:
    """Stand-in for PostgresGuardrailRuleRepository (tenant rules in 'the DB')."""

    instances: list[_FakeRepo] = []

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory
        self.rows = [_tenant_rule()]
        _FakeRepo.instances.append(self)

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        return [r for r in self.rows if r.tenant_id == tenant_id]

    async def upsert(self, rule: GuardrailRule) -> None:
        return None

    async def insert_if_absent(self, rule: GuardrailRule) -> None:
        return None


class _Store:
    def add_change_listener(self, _listener: Any) -> None: ...


@pytest.fixture(autouse=True)
def _fresh_worker_engine() -> Iterator[None]:
    """A fresh worker process: no repository bound. Restore the singleton after."""
    saved_repo, saved_auto = guardrails_engine._repo, guardrails_engine._auto_persist
    saved_rules = {k: list(v) for k, v in guardrails_engine._rules.items()}
    _FakeRepo.instances.clear()
    guardrails_engine.bind_repository(None)
    guardrails_engine._rules.pop(_TENANT, None)
    yield
    guardrails_engine.bind_repository(saved_repo, auto_persist=saved_auto)
    guardrails_engine._rules = saved_rules
    guardrails_engine._unsaved_seeds = []
    guardrails_engine._unsaved = []


@pytest.fixture
def worker_patches() -> Iterator[Any]:
    factory = MagicMock(name="worker-session-factory")
    patches = [
        patch("app.db.session.get_session_factory", return_value=factory),
        patch("app.db.session.get_system_session_factory", return_value=MagicMock()),
        patch(
            "app.providers.embedder_factory.resolve_embedder",
            return_value=EmbedderResolution(embedder=MagicMock(), dimension=8),
        ),
        patch("app.rag.store.KnowledgeStore", return_value=_Store()),
        patch("app.guardrails_v2.repository.PostgresGuardrailRuleRepository", _FakeRepo),
    ]
    for p in patches:
        p.start()
    try:
        yield factory
    finally:
        for p in reversed(patches):
            p.stop()


def test_repo_worker_services_bind_the_tenant_rule_repository(worker_patches: Any) -> None:
    from app.ingestion import repo_tasks

    repo_tasks._worker_services()

    assert guardrails_engine.has_repository
    assert len(_FakeRepo.instances) == 1
    assert _FakeRepo.instances[0].session_factory is worker_patches
    assert guardrails_engine._repo is _FakeRepo.instances[0]


def test_scheduler_and_repo_ingest_share_one_worker_binding(worker_patches: Any) -> None:
    """Both builders go through the shared helper; a second build does not rebind."""
    from app.ingestion import repo_tasks
    from app.ingestion.scheduler import _build_worker_ingestion

    repo_tasks._worker_services()
    _build_worker_ingestion()
    assert len(_FakeRepo.instances) == 1


async def test_repo_ingest_in_production_applies_tenant_block_rule(
    worker_patches: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real repo-ingest task run: the screen inside the ingestion body (the
    same ``screen_ingest_text`` call ``_ingest_repo_background`` makes per file)
    works in production and blocks the tenant's rule."""
    from app.core.config import get_settings
    from app.ingestion import repo_tasks
    from app.ingestion.pipeline import IngestionPolicyRejectedError, screen_ingest_text

    monkeypatch.setattr(get_settings(), "environment", "production")
    outcomes: dict[str, Any] = {}

    async def _body(**kwargs: Any) -> None:
        tenant_id = kwargs["tenant_ctx"].tenant_id
        outcomes["clean"] = await screen_ingest_text(
            "def add(a, b):\n    return a + b\n", tenant_id=tenant_id, doc_id="ok.py"
        )
        try:
            await screen_ingest_text(
                f"# codename {_SECRET_WORD} lives here\n", tenant_id=tenant_id, doc_id="x.py"
            )
        except IngestionPolicyRejectedError as exc:
            outcomes["blocked"] = str(exc)

    with patch("app.api.knowledge._ingest_repo_background", new=_body):
        await repo_tasks._run_repo_ingest_async(
            job_id="job-rv06",
            tenant_id=_TENANT,
            repo_url="https://github.com/example/repository",
            collection_id="c",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
        )

    assert "return a + b" in outcomes["clean"]
    assert "guardrail_blocked" in outcomes.get("blocked", "")
