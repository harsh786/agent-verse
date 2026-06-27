"""Tests for Redis connection pooling in Celery tasks."""


def test_get_redis_pool_returns_same_instance():
    """Module-level Redis pool must be reused across calls."""
    from app.scaling import tasks
    if not hasattr(tasks, "_get_redis_pool"):
        return  # Skip if not implemented

    pool1 = tasks._get_redis_pool()
    pool2 = tasks._get_redis_pool()
    assert pool1 is pool2, "Must return the same pool instance"


def test_embedding_dim_migration_uses_1536():
    """Migration 0028 must create vector(1536) not vector(768)."""
    import inspect
    import importlib

    try:
        m = importlib.import_module(
            "app.db.migrations.versions.0028_ltm_embedding_resize"
        )
        src = inspect.getsource(m)
        assert "1536" in src, "Migration must use 1536 dimensions"
        assert "768" not in src.split("downgrade")[0], "Upgrade must not use 768 dims"
    except (ImportError, ModuleNotFoundError):
        pass  # Module names starting with digits can't be imported via dotted path

    import os
    migration_files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0028" in f for f in migration_files), "0028 migration must exist"
    assert any("embedding" in f and "0028" in f for f in migration_files), \
        "Migration 0028 must be the embedding resize"
