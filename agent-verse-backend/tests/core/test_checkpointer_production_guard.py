"""The production lifespan must refuse an in-memory LangGraph checkpointer.

``AsyncRedisSaver`` needs Redis' query engine (RediSearch, the ``FT.*``
commands). Every Redis image this repo shipped — dev compose, prod compose, the
Helm chart, and the e2e testcontainer — was plain ``redis:7-alpine``, which has
no such module, so ``setup()`` raised on every boot and the lifespan fell back to
LangGraph's ``MemorySaver`` behind a single ``logger.warning``.

That fallback is not a degradation in a multi-replica deployment, it is silent
data loss: agent state lives in one process's heap, so a goal paused for HITL on
replica 1 cannot be resumed on replica 2, and a worker restart drops every
in-flight checkpoint. Nothing downstream can observe it.

The infra images are fixed; this pins the behaviour so a future Redis without
the module cannot quietly reintroduce the in-memory path in production.
"""

from __future__ import annotations

from typing import Any

import pytest


def _checkpointer_guard_source() -> str:
    """Return the lifespan's checkpointer-fallback block."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "app" / "main.py"
    text = src.read_text()
    start = text.index("async_redis_saver_failed")
    end = text.index("using_memory_saver_checkpointer_no_persistence", start)
    return text[start:end]


def test_production_refuses_the_memory_saver_fallback() -> None:
    """In production the fallback raises instead of installing a MemorySaver."""
    block = _checkpointer_guard_source()
    assert 'settings.environment == "production"' in block, (
        "the checkpointer fallback no longer special-cases production — a "
        "production boot can silently land on an in-process MemorySaver again"
    )
    raise_idx = block.index("raise RuntimeError")
    memsaver_idx = block.index("MemorySaver()")
    assert raise_idx < memsaver_idx, (
        "the production guard must short-circuit BEFORE MemorySaver is installed"
    )


@pytest.mark.parametrize(
    "compose_file",
    [
        "infra/docker-compose.yml",
        "infra/docker-compose.prod.yml",
        "infra/docker-compose.e2e.yml",
    ],
)
def test_compose_redis_has_the_query_engine(compose_file: str) -> None:
    """The primary Redis service must run an image that ships RediSearch."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    config: dict[str, Any] = yaml.safe_load((root / compose_file).read_text())
    redis_svc = config["services"]["redis"]
    image = str(redis_svc["image"])
    assert "redis-stack" in image, (
        f"{compose_file}: redis image {image!r} has no query engine (RediSearch), "
        "so the LangGraph checkpointer falls back to in-process memory"
    )
    # redis-stack-server loads its modules from its own entrypoint; overriding
    # `command:` with a bare redis-server invocation skips them, which would
    # reintroduce the module-less Redis this test exists to prevent.
    assert "command" not in redis_svc, (
        f"{compose_file}: overriding `command` on redis-stack-server skips module "
        "loading — pass extra flags via the REDIS_ARGS environment variable"
    )


def test_helm_redis_has_the_query_engine() -> None:
    """The Helm chart deploys multiple replicas; it especially cannot use plain Redis."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    values: dict[str, Any] = yaml.safe_load(
        (root / "infra/helm/agentverse/values.yaml").read_text()
    )
    image = str(values["redis"]["image"])
    assert "redis-stack" in image, (
        f"helm values.yaml: redis image {image!r} has no query engine (RediSearch)"
    )
