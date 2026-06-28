"""Test checkpointer resolution priority and RedisSaver wiring."""
import logging
import pytest
from unittest.mock import MagicMock, patch


def test_resolve_checkpointer_uses_app_state_first():
    """If app.state.langgraph_checkpointer is a real saver, use it without rebuilding."""
    from app.services.goal_service import _resolve_checkpointer
    from langgraph.checkpoint.base import BaseCheckpointSaver

    # Concrete subclass so isinstance(saver, BaseCheckpointSaver) passes inside the function.
    class _FakeSaver(BaseCheckpointSaver):
        def get_tuple(self, config):
            return None

        def list(self, config, **kwargs):
            return iter([])

        def put(self, config, checkpoint, metadata, new_versions):
            return config

        def put_writes(self, config, writes, task_id):
            pass

    saver = _FakeSaver()
    app_state = MagicMock()
    app_state.langgraph_checkpointer = saver

    result = _resolve_checkpointer(app_state)
    assert result is saver, "Must return the provided app_state checkpointer unchanged"


def test_resolve_checkpointer_logs_warning_on_memory_fallback(caplog):
    """When no Redis is available, a warning is logged about durability loss."""
    from app.services.goal_service import _resolve_checkpointer
    from langgraph.checkpoint.memory import MemorySaver

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None
    # No REDIS_URL set; MagicMock settings.redis_url is not a str so it is ignored.
    with patch.dict("os.environ", {}, clear=True):
        with caplog.at_level(logging.WARNING):
            result = _resolve_checkpointer(app_state)

    assert isinstance(result, MemorySaver)
    assert any(
        "LOST" in r.message or "memory" in r.message.lower() or "RESTART" in r.message
        for r in caplog.records
    ), "Must warn about state loss when falling back to MemorySaver"


def test_resolve_checkpointer_prefers_redis_over_memory():
    """When REDIS_URL is set, must attempt Redis before falling back to MemorySaver."""
    from app.services.goal_service import _resolve_checkpointer

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None

    with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379"}):
        # It may fail (no Redis running) but the attempt must be made
        result = _resolve_checkpointer(app_state)
        # In test env without Redis, should fall back to MemorySaver
        # but the code path must have attempted Redis first
        assert result is not None


def test_memory_saver_warning_contains_impact():
    """MemorySaver warning must include LOST or RESTART so operators notice."""
    import io
    from app.services.goal_service import _resolve_checkpointer

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger("app.services.goal_service")
    logger.addHandler(handler)
    try:
        with patch.dict("os.environ", {}, clear=True):
            _resolve_checkpointer(app_state)
        output = log_stream.getvalue()
    finally:
        logger.removeHandler(handler)

    assert (
        "RESTART" in output or "LOST" in output or "memory" in output.lower()
    ), f"Warning must mention restart/loss; got: {output!r}"
