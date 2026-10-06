"""a04-F074-05: every worker process counts embedding usage, not only ingestion tasks.

Shared (Redis) embedding-usage counting used to be enabled only inside the
worker ingestion builder, so a worker child whose first embed came from a
re-embed (``embed_metered``) or an agent's retrieval recorded nothing in
``emb:usage:<tenant>``. Counting is now enabled when the worker process starts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.embedding import usage
from app.scaling import celery_app as celery_mod


@pytest.fixture(autouse=True)
def _reset_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "_redis", None)
    monkeypatch.setattr(usage, "_from_env", False)
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(worker_preload_retrieval_models=False),
    )


def test_worker_child_start_enables_shared_usage_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379/0")

    celery_mod._on_worker_process_init()

    assert usage._from_env is True


def test_worker_main_process_start_enables_counting_for_solo_and_thread_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # worker_process_init only fires in prefork children; a solo / threads pool
    # runs tasks in the main process, which worker_init covers (prefork children
    # inherit the flag across the fork).
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379/0")

    celery_mod._on_worker_init_embedding_usage()

    assert usage._from_env is True


def test_worker_without_redis_env_leaves_counting_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REDIS_URL", "REDIS_SENTINEL_URLS", "REDIS_CLUSTER_NODES"):
        monkeypatch.delenv(name, raising=False)

    celery_mod._on_worker_process_init()

    assert usage._from_env is False


def test_a_usage_enable_failure_never_fails_worker_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise RuntimeError("redis env unreadable")

    monkeypatch.setattr(usage, "configure_usage_redis_from_env", _boom)

    celery_mod._on_worker_process_init()  # does not raise
    celery_mod._on_worker_init_embedding_usage()
