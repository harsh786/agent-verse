"""Tests for SSE resume-from-sequence."""
import pytest


class TestEventStoreSince:
    def test_event_store_has_list_events_since(self):
        from app.services.event_store import EventStore
        assert hasattr(EventStore, "list_events_since"), \
            "EventStore must have list_events_since method"

    @pytest.mark.asyncio
    async def test_list_events_since_returns_list(self):
        from app.services.event_store import EventStore
        store = EventStore()
        result = await store.list_events_since("fake-goal", after_sequence=0)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_list_events_since_no_db_returns_empty(self):
        from app.services.event_store import EventStore
        store = EventStore(db_session_factory=None)
        result = await store.list_events_since("some-goal", after_sequence=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_list_events_since_no_tenant_ctx_returns_empty(self):
        from app.services.event_store import EventStore

        async def fake_db():  # pragma: no cover
            pass

        store = EventStore(db_session_factory=fake_db)
        result = await store.list_events_since("some-goal", after_sequence=2, tenant_ctx=None)
        assert result == []


class TestGoalServiceSubscribeEventsSinceSequence:
    def test_subscribe_events_accepts_since_sequence(self):
        import inspect

        from app.services.goal_service import GoalService
        sig = inspect.signature(GoalService.subscribe_events)
        assert "since_sequence" in sig.parameters, \
            "subscribe_events must accept since_sequence parameter"

    def test_subscribe_events_since_sequence_defaults_to_zero(self):
        import inspect

        from app.services.goal_service import GoalService
        sig = inspect.signature(GoalService.subscribe_events)
        param = sig.parameters["since_sequence"]
        assert param.default == 0, \
            "since_sequence default must be 0"


class TestSSEEndpointLastEventId:
    def test_goals_api_reads_last_event_id(self):
        """SSE endpoint must parse Last-Event-ID header."""
        import inspect

        from app.api import goals as goals_module
        source = inspect.getsource(goals_module)
        assert "Last-Event-ID" in source or "last_event_id" in source.lower(), \
            "SSE endpoint does not read Last-Event-ID header"

    def test_sse_emits_id_lines(self):
        """SSE response must include id: lines for resume."""
        import inspect

        from app.api import goals as goals_module
        source = inspect.getsource(goals_module)
        assert (
            '"id:"' in source
            or "'id:'" in source
            or "f'id:" in source
            or 'f"id:' in source
        ), "SSE endpoint does not emit id: lines"


class TestAutoscaleSignal:
    def test_metrics_has_record_desired_workers(self):
        from app.observability import metrics
        assert hasattr(metrics, "record_desired_workers"), \
            "record_desired_workers not in metrics module"


class TestLoadTestFiles:
    def test_k6_goal_submission_exists(self):
        from pathlib import Path
        p = Path(__file__).parent.parent.parent / "infra" / "loadtest" / "goal_submission.js"
        assert p.exists(), f"k6 goal submission load test missing: {p}"

    def test_k6_sse_stream_exists(self):
        from pathlib import Path
        p = Path(__file__).parent.parent.parent / "infra" / "loadtest" / "sse_stream.js"
        assert p.exists(), f"k6 SSE stream load test missing: {p}"

    def test_locustfile_exists(self):
        from pathlib import Path
        p = Path(__file__).parent.parent.parent / "infra" / "loadtest" / "locustfile.py"
        assert p.exists(), f"locust file missing: {p}"
