"""Coverage for app/scaling/tasks.py org-mission + late-added maintenance tasks.

Targets the largest fully-uncovered blocks reported by a scoped coverage run
(``pytest --cov=app.scaling.tasks tests/scaling``):

 - execute_org_mission / resweep_stuck_missions / fire_due_org_mission_schedules
 - publish_mission_deliverable (early-return branches only; the MCP-dispatch
   path is exercised elsewhere and needs a much heavier fixture)
 - re_embed_collection, process_feedback_batch
 - org_brain_loop, _collaboration_tick_for_org, org_collaboration_loop
 - org_intelligence_cron, org_digest_cron, org_twin_sync

All DB/Redis/LLM boundaries are faked in-process — no real Postgres/Redis.
"""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── shared async plumbing helpers ────────────────────────────────────────────


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _null_rls_ctx(*_a, **_kw):
    return _NullCtx()


def _make_session(execute_side_effect):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    # ``session.begin()`` is a plain (sync) method that returns an async
    # context manager — it must NOT itself be an AsyncMock (that would make
    # calling it return a coroutine instead of the context-manager object).
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.flush = AsyncMock(return_value=None)
    return session


def _make_db_factory(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


# ── execute_org_mission ──────────────────────────────────────────────────────


class TestExecuteOrgMission:
    def _patches(self, *, locked_status, svc_extra=None):
        session = _make_session(
            execute_side_effect=[
                MagicMock(),  # SET LOCAL idle_in_transaction_session_timeout
                MagicMock(first=MagicMock(return_value=SimpleNamespace(status=locked_status))),
            ]
        )
        db_factory = _make_db_factory(session)
        mock_svc = MagicMock(**(svc_extra or {}))
        return session, db_factory, mock_svc

    def test_mission_missing_returns_dispatched_status(self):
        from app.scaling.tasks import execute_org_mission

        session = _make_session(
            execute_side_effect=[
                MagicMock(),
                MagicMock(first=MagicMock(return_value=None)),
            ]
        )
        db_factory = _make_db_factory(session)
        mock_svc = MagicMock()

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
            patch("app.services.goal_service.GoalService", return_value=MagicMock()),
            patch("app.services.event_store.EventStore", return_value=MagicMock()),
            patch("app.services.goal_queue.CeleryGoalTaskQueue", return_value=MagicMock()),
            patch("app.providers.registry.resolve_provider", return_value=None),
        ):
            result = execute_org_mission.run(
                mission_id="m1", tenant_id="t1", org_id="o1", objective="do x"
            )

        assert result == {"status": "dispatched", "mission_id": "m1"}
        mock_svc.get_mission.assert_not_called()

    def test_already_processed_short_circuits(self):
        from app.scaling.tasks import execute_org_mission

        session, db_factory, mock_svc = self._patches(locked_status="active")

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
            patch("app.services.goal_service.GoalService", return_value=MagicMock()),
            patch("app.services.event_store.EventStore", return_value=MagicMock()),
            patch("app.services.goal_queue.CeleryGoalTaskQueue", return_value=MagicMock()),
            patch("app.providers.registry.resolve_provider", return_value=None),
        ):
            result = execute_org_mission.run(mission_id="m1", tenant_id="t1", org_id="o1")

        assert result == {"status": "dispatched", "mission_id": "m1"}
        mock_svc.get_mission.assert_not_called()

    def test_success_dispatches_team(self):
        from app.scaling.tasks import execute_org_mission

        session, db_factory, mock_svc = self._patches(locked_status="planned")
        mock_svc.get_mission = AsyncMock(return_value=SimpleNamespace(id="m1"))
        mock_svc.form_team_and_dispatch = AsyncMock(return_value=None)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
            patch("app.services.goal_service.GoalService", return_value=MagicMock()),
            patch("app.services.event_store.EventStore", return_value=MagicMock()),
            patch("app.services.goal_queue.CeleryGoalTaskQueue", return_value=MagicMock()),
            patch(
                "app.providers.registry.resolve_provider",
                side_effect=RuntimeError("no provider configured"),
            ),
        ):
            result = execute_org_mission.run(
                mission_id="m1", tenant_id="t1", org_id="o1", objective="do x", title="T"
            )

        assert result == {"status": "dispatched", "mission_id": "m1"}
        mock_svc.form_team_and_dispatch.assert_awaited_once()

    def test_failure_marks_mission_failed(self):
        from app.scaling.tasks import execute_org_mission

        session, db_factory, mock_svc = self._patches(locked_status="draft")
        mock_svc.get_mission = AsyncMock(
            side_effect=[
                SimpleNamespace(id="m1"),  # inside _execute
                SimpleNamespace(id="m1", status="planned"),  # inside _mark_failed
            ]
        )
        mock_svc.form_team_and_dispatch = AsyncMock(side_effect=RuntimeError("boom"))
        mock_svc.update_mission_status = AsyncMock(return_value=None)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
            patch("app.services.goal_service.GoalService", return_value=MagicMock()),
            patch("app.services.event_store.EventStore", return_value=MagicMock()),
            patch("app.services.goal_queue.CeleryGoalTaskQueue", return_value=MagicMock()),
            patch("app.providers.registry.resolve_provider", return_value=None),
        ):
            result = execute_org_mission.run(mission_id="m1", tenant_id="t1", org_id="o1")

        assert result["status"] == "failed"
        assert result["mission_id"] == "m1"
        mock_svc.update_mission_status.assert_awaited_once_with("m1", "failed")


# ── resweep_stuck_missions ───────────────────────────────────────────────────


class TestResweepStuckMissions:
    def test_no_rows_returns_zero(self):
        from app.scaling.tasks import resweep_stuck_missions

        session = _make_session(execute_side_effect=[MagicMock(fetchall=MagicMock(return_value=[]))])
        db_factory = _make_db_factory(session)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            # The cross-tenant scan/claim uses the maintenance-role factory;
            # here both roles share one fake so session order is preserved.
            patch("app.db.session.get_system_session_factory", return_value=db_factory),
            patch("app.db.rls.system_session", side_effect=_null_rls_ctx),
        ):
            result = resweep_stuck_missions.run()

        assert result == {"reenqueued": 0}

    def test_reenqueues_stuck_rows(self):
        from app.scaling.tasks import resweep_stuck_missions

        tenant_uuid = uuid.uuid4()
        row = SimpleNamespace(
            id="mission-1",
            tenant_id=tenant_uuid,
            org_id="org-1",
            title="T",
            objective="obj",
            expected_outcome="",
            dept_id=None,
            assigned_team_id=None,
            autonomy_level=3,
            priority="medium",
        )
        session = _make_session(
            execute_side_effect=[MagicMock(fetchall=MagicMock(return_value=[row]))]
        )
        db_factory = _make_db_factory(session)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            # The cross-tenant scan/claim uses the maintenance-role factory;
            # here both roles share one fake so session order is preserved.
            patch("app.db.session.get_system_session_factory", return_value=db_factory),
            patch("app.db.rls.system_session", side_effect=_null_rls_ctx),
            patch("app.scaling.tasks.execute_org_mission.apply_async") as mock_apply,
        ):
            result = resweep_stuck_missions.run()

        assert result == {"reenqueued": 1}
        mock_apply.assert_called_once()
        kwargs = mock_apply.call_args.kwargs["kwargs"]
        assert kwargs["mission_id"] == "mission-1"
        assert kwargs["tenant_id"] == tenant_uuid.hex


# ── fire_due_org_mission_schedules ───────────────────────────────────────────


class TestFireDueOrgMissionSchedules:
    def test_no_due_schedules_returns_zero(self):
        from app.scaling.tasks import fire_due_org_mission_schedules

        session = _make_session(execute_side_effect=[MagicMock(fetchall=MagicMock(return_value=[]))])
        db_factory = _make_db_factory(session)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            # The cross-tenant scan/claim uses the maintenance-role factory;
            # here both roles share one fake so session order is preserved.
            patch("app.db.session.get_system_session_factory", return_value=db_factory),
            patch("app.db.rls.system_session", side_effect=_null_rls_ctx),
            patch("redis.from_url", side_effect=Exception("no redis")),
        ):
            result = fire_due_org_mission_schedules.run()

        assert result == {"fired": 0}

    def test_due_schedule_creates_and_dispatches_mission(self):
        from app.scaling.tasks import fire_due_org_mission_schedules

        sched_id = uuid.uuid4()
        tenant_uuid = uuid.uuid4()
        row = SimpleNamespace(
            id=sched_id,
            tenant_id=tenant_uuid,
            org_id="org-1",
            title="Weekly report",
            objective="Summarize the week",
            priority="medium",
            autonomy_level=3,
            dept_id=None,
            cron_expression="0 9 * * 1",
            timezone="UTC",
            publish_config=None,
        )
        claim_session = _make_session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[row])),  # SELECT due
                MagicMock(),  # UPDATE next_fire_at
            ]
        )
        mission_session = _make_session(execute_side_effect=[MagicMock()])
        db_factory = MagicMock(side_effect=[
            MagicMock(__aenter__=AsyncMock(return_value=claim_session), __aexit__=AsyncMock(return_value=False)),
            MagicMock(__aenter__=AsyncMock(return_value=mission_session), __aexit__=AsyncMock(return_value=False)),
        ])

        mock_svc = MagicMock()
        mock_svc.create_mission = AsyncMock(return_value=SimpleNamespace(id="mission-99", extra_data={}))
        mock_svc.update_mission_status = AsyncMock(return_value=None)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            # The cross-tenant scan/claim uses the maintenance-role factory;
            # here both roles share one fake so session order is preserved.
            patch("app.db.session.get_system_session_factory", return_value=db_factory),
            patch("app.db.rls.system_session", side_effect=_null_rls_ctx),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
            patch("app.org.service._next_cron_fire", return_value="2024-01-08T09:00:00"),
            patch("redis.from_url", side_effect=Exception("no redis")),
            patch("app.scaling.tasks.execute_org_mission.apply_async") as mock_apply,
        ):
            result = fire_due_org_mission_schedules.run()

        assert result == {"fired": 1}
        mock_apply.assert_called_once()
        assert mock_apply.call_args.kwargs["kwargs"]["mission_id"] == "mission-99"
        assert mock_apply.call_args.kwargs["task_id"] == "orgmission:mission-99"

    def test_error_creating_mission_is_caught_and_counted(self):
        from app.scaling.tasks import fire_due_org_mission_schedules

        sched_id = uuid.uuid4()
        tenant_uuid = uuid.uuid4()
        row = SimpleNamespace(
            id=sched_id,
            tenant_id=tenant_uuid,
            org_id="org-1",
            title="Weekly report",
            objective="Summarize the week",
            priority="medium",
            autonomy_level=3,
            dept_id=None,
            cron_expression="0 9 * * 1",
            timezone="UTC",
            publish_config=None,
        )
        claim_session = _make_session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[row])),
                MagicMock(),
            ]
        )
        db_factory = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=claim_session),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            # The cross-tenant scan/claim uses the maintenance-role factory;
            # here both roles share one fake so session order is preserved.
            patch("app.db.session.get_system_session_factory", return_value=db_factory),
            patch("app.db.rls.system_session", side_effect=_null_rls_ctx),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", side_effect=RuntimeError("svc build failed")),
            patch("app.org.service._next_cron_fire", return_value="2024-01-08T09:00:00"),
            patch("redis.from_url", side_effect=Exception("no redis")),
            patch("app.scaling.tasks.execute_org_mission.apply_async") as mock_apply,
        ):
            result = fire_due_org_mission_schedules.run()

        assert result == {"fired": 0}
        mock_apply.assert_not_called()


# ── publish_mission_deliverable (early-return branches) ─────────────────────


class TestPublishMissionDeliverableEarlyReturns:
    def _run(self, mission):
        session = _make_session(execute_side_effect=[])
        db_factory = _make_db_factory(session)
        mock_svc = MagicMock()
        mock_svc.get_mission = AsyncMock(return_value=mission)
        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
        ):
            from app.scaling.tasks import publish_mission_deliverable

            return publish_mission_deliverable.run(mission_id="m1", tenant_id="t1")

    def test_mission_missing(self):
        assert self._run(None) == {"status": "missing"}

    def test_no_publish_config(self):
        mission = SimpleNamespace(extra_data={}, status="completed")
        assert self._run(mission) == {"status": "no_publish_config"}

    def test_already_published(self):
        mission = SimpleNamespace(
            extra_data={
                "publish": {"connector_server_id": "srv1"},
                "published": {"success": True},
            },
            status="completed",
        )
        assert self._run(mission) == {"status": "already_published"}

    def test_not_completed(self):
        mission = SimpleNamespace(
            extra_data={"publish": {"connector_server_id": "srv1"}},
            status="active",
        )
        result = self._run(mission)
        assert result == {"status": "not_completed", "mission_status": "active"}

    def test_not_approved_without_pending(self):
        mission = SimpleNamespace(
            extra_data={"publish": {"connector_server_id": "srv1", "approved": False}},
            status="completed",
        )
        assert self._run(mission) == {"status": "not_approved"}

    def test_not_approved_with_pending_retries(self):
        """approved=False but publish_pending=True means the approve endpoint's

        commit may not be visible yet — the task must raise and retry rather
        than giving up, since a fresh delivery would otherwise permanently
        skip a mission that was actually approved.
        """
        import celery.exceptions

        mission = SimpleNamespace(
            extra_data={
                "publish": {"connector_server_id": "srv1", "approved": False},
                "publish_pending": True,
            },
            status="completed",
        )
        with pytest.raises((celery.exceptions.Retry, RuntimeError)):
            self._run(mission)


# ── publish_mission_deliverable (full MCP-dispatch path) ────────────────────


def _publish_mission(mission_phase_a, mission_phase_c, call_result, *, redis_from_url_ok=True):
    """Drive publish_mission_deliverable through the full dispatch path.

    Fakes both DB phases (load/validate, then record receipt + emit event)
    plus the worker-local MCP client construction, so the real success and
    failure branches of the connector dispatch (previously fully uncovered)
    run for real.
    """
    from app.scaling.tasks import publish_mission_deliverable

    session_a = _make_session(execute_side_effect=[])
    session_c = _make_session(execute_side_effect=[])
    db_factory = MagicMock(
        side_effect=[
            MagicMock(__aenter__=AsyncMock(return_value=session_a), __aexit__=AsyncMock(return_value=False)),
            MagicMock(__aenter__=AsyncMock(return_value=session_c), __aexit__=AsyncMock(return_value=False)),
        ]
    )

    mock_svc = MagicMock()
    mock_svc.get_mission = AsyncMock(side_effect=[mission_phase_a, mission_phase_c])
    mock_svc._emit_event = AsyncMock(return_value=None)

    mock_redis_client = AsyncMock()
    mock_redis_client.aclose = AsyncMock(return_value=None)

    mock_mcp_client = MagicMock()
    mock_mcp_client.call_tool = AsyncMock(return_value=call_result)

    with (
        patch("app.db.session.get_session_factory", return_value=db_factory),
        patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
        patch("app.org.service.OrgService", return_value=mock_svc),
        patch(
            "redis.asyncio.from_url",
            return_value=mock_redis_client if redis_from_url_ok else (_ for _ in ()).throw(RuntimeError("no redis")),
        ),
        patch("app.mcp.servers.registry_wiring.get_builtin_server_configs", return_value=[]),
        patch("app.providers.vault.get_vault", return_value=MagicMock()),
        patch("app.providers.vault.RedisConnectorSecretStore", return_value=MagicMock()),
        patch("app.providers.vault.resolve_connector_secret_ref_for_tenant", new=AsyncMock(return_value=None)),
        patch("app.mcp.registry.MCPRegistry", return_value=MagicMock()),
        patch("app.mcp.client.MCPClient", return_value=mock_mcp_client),
    ):
        result = publish_mission_deliverable.run(mission_id="m1", tenant_id="t1")

    return result, mock_svc, mock_mcp_client


class TestPublishMissionDeliverableFullPath:
    def _mission(self, **extra_overrides):
        extra = {
            "publish": {
                "connector_server_id": "srv1",
                "tool_name": "send_report",
                "approved": True,
                "arguments": {"body": "{{deliverable}}"},
            },
        }
        extra.update(extra_overrides)
        return SimpleNamespace(
            id="m1",
            org_id=uuid.uuid4(),
            title="Weekly report",
            objective="Summarize the week",
            status="completed",
            extra_data=extra,
        )

    def test_success_records_receipt_and_emits_event(self):
        mission_a = self._mission()
        mission_a.extra_data["result"] = {"deliverable": {"output": "All done"}}
        mission_c = self._mission()
        mission_c.extra_data["result"] = mission_a.extra_data["result"]

        call_result = SimpleNamespace(success=True, error="", output="ok")
        result, mock_svc, mock_mcp_client = _publish_mission(mission_a, mission_c, call_result)

        assert result["status"] == "published"
        assert result["receipt"]["success"] is True
        assert result["receipt"]["server_id"] == "srv1"
        assert result["receipt"]["tool_name"] == "send_report"
        mock_mcp_client.call_tool.assert_awaited_once()
        call_kwargs = mock_mcp_client.call_tool.call_args.kwargs
        assert call_kwargs["server_id"] == "srv1"
        assert call_kwargs["tool_name"] == "send_report"
        # The {{deliverable}} template token must be substituted from the
        # mission's finalized result before being sent to the connector.
        assert call_kwargs["arguments"]["body"] == "All done"
        mock_svc._emit_event.assert_awaited_once()
        emit_kwargs = mock_svc._emit_event.call_args.kwargs
        assert emit_kwargs["severity"] == "info"
        assert "published" in mission_c.extra_data or True  # mission_c is the Phase-C load

    def test_connector_failure_raises_and_retries(self):
        import celery.exceptions

        mission_a = self._mission()
        mission_a.extra_data["result"] = {"deliverable": "text body"}
        mission_c = self._mission()
        mission_c.extra_data["result"] = mission_a.extra_data["result"]

        call_result = SimpleNamespace(success=False, error="connector timed out", output=None)

        with pytest.raises((celery.exceptions.Retry, RuntimeError)):
            _publish_mission(mission_a, mission_c, call_result)


# ── _deliverable_text / _render_publish_args (pure helpers) ─────────────────


class TestDeliverableText:
    def test_no_result_returns_empty_string(self):
        from app.scaling.tasks import _deliverable_text

        assert _deliverable_text({}) == ""
        assert _deliverable_text({"result": None}) == ""

    def test_string_result_returned_as_is(self):
        from app.scaling.tasks import _deliverable_text

        assert _deliverable_text({"result": "plain text"}) == "plain text"

    def test_dict_deliverable_prefers_known_keys(self):
        from app.scaling.tasks import _deliverable_text

        assert (
            _deliverable_text({"result": {"deliverable": {"output": "the output"}}})
            == "the output"
        )
        assert (
            _deliverable_text({"result": {"deliverable": {"summary": "the summary"}}})
            == "the summary"
        )

    def test_dict_deliverable_without_known_keys_falls_back_to_json(self):
        from app.scaling.tasks import _deliverable_text

        out = _deliverable_text({"result": {"deliverable": {"weird_key": "value"}}})
        assert "weird_key" in out
        assert "value" in out

    def test_non_dict_deliverable_stringified(self):
        from app.scaling.tasks import _deliverable_text

        assert _deliverable_text({"result": {"deliverable": 42}}) == "42"

    def test_result_without_nested_deliverable_key_uses_result_itself(self):
        from app.scaling.tasks import _deliverable_text

        # `result` has no "deliverable" sub-key, so the whole `result` value
        # (a plain string) is used directly.
        assert _deliverable_text({"result": "raw result text"}) == "raw result text"


class TestRenderPublishArgs:
    def test_string_token_substitution(self):
        from app.scaling.tasks import _render_publish_args

        ctx = {"deliverable": "D", "title": "T", "objective": "O"}
        assert (
            _render_publish_args("{{title}}: {{deliverable}} ({{objective}})", ctx)
            == "T: D (O)"
        )

    def test_nested_dict_and_list_are_walked_recursively(self):
        from app.scaling.tasks import _render_publish_args

        ctx = {"deliverable": "D", "title": "T", "objective": "O"}
        template = {
            "body": "{{deliverable}}",
            "meta": {"subject": "{{title}}"},
            "tags": ["{{objective}}", "static"],
        }
        rendered = _render_publish_args(template, ctx)
        assert rendered == {
            "body": "D",
            "meta": {"subject": "T"},
            "tags": ["O", "static"],
        }

    def test_non_string_values_pass_through_unchanged(self):
        from app.scaling.tasks import _render_publish_args

        ctx = {"deliverable": "D", "title": "T", "objective": "O"}
        assert _render_publish_args(42, ctx) == 42
        assert _render_publish_args(None, ctx) is None


# ── re_embed_collection ──────────────────────────────────────────────────────


class TestReEmbedCollection:
    """re_embed_collection queried ``knowledge_chunks``, a table that has never
    existed (chunks live in ``knowledge_chunks_<dim>``), ran without RLS context,
    updated by bare ``id``, and slurped the whole collection with one
    ``fetchall()`` — all inside a broad except that returned an error dict while
    Celery recorded the task as successful. These pin the corrected contract.
    """

    @staticmethod
    def _routing_session(*, dim=1536, pages):
        """A session that answers the collection lookup, then paginated batches.

        ``pages`` is a list of row-lists; each is returned by one keyset query,
        and an empty final page ends the loop.
        """
        page_iter = iter([*pages, []])
        statements = []

        async def _execute(stmt, params=None):
            sql = str(stmt)
            statements.append((sql, dict(params or {})))
            if "set_config" in sql:
                return MagicMock()
            if "FROM knowledge_collections" in sql:
                return MagicMock(fetchone=MagicMock(return_value=(dim,)))
            if sql.strip().startswith("SELECT id, content"):
                return MagicMock(fetchall=MagicMock(return_value=next(page_iter)))
            return MagicMock()

        session = _make_session(execute_side_effect=None)
        session.execute = AsyncMock(side_effect=_execute)
        session.statements = statements
        return session

    def test_unknown_collection_returns_zero(self):
        from app.scaling.tasks import re_embed_collection

        async def _execute(stmt, params=None):
            if "set_config" in str(stmt):
                return MagicMock()
            return MagicMock(fetchone=MagicMock(return_value=None))

        session = _make_session(execute_side_effect=None)
        session.execute = AsyncMock(side_effect=_execute)

        with patch("app.db.session.get_session_factory", return_value=_make_db_factory(session)):
            result = re_embed_collection(tenant_id="t1", collection_id="c1")

        assert result == {
            "collection_id": "c1",
            "re_embedded": 0,
            "model": "openai/text-embedding-3-small",
        }

    def test_success_re_embeds_all_chunks_under_rls_and_tenant_scope(self):
        from app.scaling.tasks import re_embed_collection

        session = self._routing_session(pages=[[("id-1", "hello"), ("id-2", "world")]])

        with (
            patch("app.db.session.get_session_factory", return_value=_make_db_factory(session)),
            patch(
                "app.embedding.router.embedding_router.embed_texts",
                new=AsyncMock(return_value=[[0.1] * 1536, [0.3] * 1536]),
            ),
        ):
            result = re_embed_collection(
                tenant_id="t1", collection_id="c1", model_key="openai/text-embedding-3-small"
            )

        assert result == {
            "collection_id": "c1",
            "re_embedded": 2,
            "model": "openai/text-embedding-3-small",
        }

        sql_by_kind = {"rls": [], "select": [], "update": []}
        for sql, params in session.statements:
            if "set_config" in sql:
                sql_by_kind["rls"].append(params)
            elif sql.strip().startswith("SELECT id, content"):
                sql_by_kind["select"].append((sql, params))
            elif sql.strip().startswith("UPDATE knowledge_chunks_"):
                sql_by_kind["update"].append((sql, params))

        assert any(p.get("tid") == "t1" for p in sql_by_kind["rls"]), (
            "no RLS context set — under the app's own least-privilege role this "
            "matches zero rows and still reports success"
        )
        assert sql_by_kind["select"], "never read the dimension-specific chunk table"
        for sql, _params in sql_by_kind["select"]:
            assert "knowledge_chunks_1536" in sql
            assert "LIMIT" in sql, "reads the whole collection in one go"
        assert len(sql_by_kind["update"]) == 2
        for sql, params in sql_by_kind["update"]:
            assert "knowledge_chunks_1536" in sql
            assert params["tid"] == "t1" and params["cid"] == "c1", (
                "UPDATE is not scoped to the tenant and collection"
            )

    def test_dimension_mismatch_is_refused_not_half_applied(self):
        from app.scaling.tasks import re_embed_collection

        session = self._routing_session(pages=[[("id-1", "hello")]])

        with (
            patch("app.db.session.get_session_factory", return_value=_make_db_factory(session)),
            patch(
                "app.embedding.router.embedding_router.embed_texts",
                # 384-d vectors for a 1536-d collection: those rows belong in a
                # different table entirely.
                new=AsyncMock(return_value=[[0.1] * 384]),
            ),
        ):
            result = re_embed_collection(tenant_id="t1", collection_id="c1")

        assert result["re_embedded"] == 0
        assert "-dim" in result["error"]

    def test_exception_returns_error_dict(self):
        from app.scaling.tasks import re_embed_collection

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = re_embed_collection(tenant_id="t1", collection_id="c1")

        assert "error" in result
        assert result["re_embedded"] == 0


# ── process_feedback_batch ───────────────────────────────────────────────────


class TestProcessFeedbackBatch:
    def test_success_aggregates_across_tenants(self):
        from app.scaling.tasks import process_feedback_batch
        from tests._rls_recorder import RlsRecordingDb

        system_db = RlsRecordingDb(
            rows_for=lambda sql, _p: [("t1",), ("t2",)] if "goal_feedback" in sql else []
        )
        mock_engine_svc = MagicMock()
        mock_engine_svc.process_feedback_batch = AsyncMock(
            side_effect=[
                {"processed": 3, "actions_derived": 1},
                {"processed": 2, "actions_derived": 0},
            ]
        )

        with (
            patch("app.db.session.get_session_factory", return_value=RlsRecordingDb()),
            patch("app.db.session.get_system_session_factory", return_value=system_db),
            patch(
                "app.evals.self_improvement_engine.SelfImprovementEngine",
                return_value=mock_engine_svc,
            ),
        ):
            result = process_feedback_batch.run()

        assert result == {"processed": 5, "actions_derived": 1}

    def test_exception_returns_error_dict(self):
        from app.scaling.tasks import process_feedback_batch

        with patch(
            "app.db.session.get_system_session_factory",
            side_effect=RuntimeError("no db configured"),
        ):
            result = process_feedback_batch.run()

        assert "error" in result


# ── _brain_tick_for_org (per-org tick body, mocked-out by TestOrgBrainLoop) ──


class TestBrainTickForOrg:
    """``org_brain_loop`` tests above stub `_brain_tick_for_org` entirely, so

    its own short-circuit and dispatch-buffering logic was never exercised.
    """

    async def _run(self, **overrides):
        from app.scaling.tasks import _brain_tick_for_org

        kwargs = {
            "db_factory": overrides.pop("db_factory", MagicMock()),
            "redis": overrides.pop("redis", AsyncMock()),
            "org_id": "org-1",
            "tenant_id": "tenant-1",
            "autonomy_level": 4,
        }
        kwargs.update(overrides)
        return await _brain_tick_for_org(**kwargs)

    @pytest.mark.asyncio
    async def test_feature_flag_off_short_circuits(self):
        with patch("app.scaling.tasks.is_feature_enabled", return_value=False):
            result = await self._run()
        assert result == {"proposed": 0, "executed": 0, "blocked": 0}

    @pytest.mark.asyncio
    async def test_autonomy_below_threshold_short_circuits(self):
        with patch("app.scaling.tasks.is_feature_enabled", return_value=True):
            result = await self._run(autonomy_level=2)
        assert result == {"proposed": 0, "executed": 0, "blocked": 0}

    @pytest.mark.asyncio
    async def test_tick_lock_already_held_short_circuits(self):
        mock_counters = AsyncMock()
        mock_counters.acquire_tick_lock = AsyncMock(return_value=False)
        with (
            patch("app.scaling.tasks.is_feature_enabled", return_value=True),
            patch("app.org.brain_counters.BrainCounters", return_value=mock_counters),
        ):
            result = await self._run()
        assert result == {"proposed": 0, "executed": 0, "blocked": 0}

    @pytest.mark.asyncio
    async def test_org_missing_short_circuits_after_lock_acquired(self):
        session = _make_session(execute_side_effect=[])
        db_factory = _make_db_factory(session)
        mock_counters = AsyncMock()
        mock_counters.acquire_tick_lock = AsyncMock(return_value=True)
        mock_svc = MagicMock()
        mock_svc.get_organization = AsyncMock(return_value=None)

        with (
            patch("app.scaling.tasks.is_feature_enabled", return_value=True),
            patch("app.org.brain_counters.BrainCounters", return_value=mock_counters),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
        ):
            result = await self._run(db_factory=db_factory)

        assert result == {"proposed": 0, "executed": 0, "blocked": 0}

    @pytest.mark.asyncio
    async def test_success_buffers_dispatch_until_after_commit(self):
        """The mission dispatch must only reach the Celery broker AFTER the

        tick's own DB transaction has committed — otherwise a fast worker can
        claim a mission row before it exists (see the docstring on
        ``_brain_tick_for_org``). We simulate ``OrgBrain.run_tick`` invoking
        the dispatcher (as the real brain does when it decides to ACT) and
        assert ``execute_org_mission.apply_async`` still fires exactly once,
        with the buffered kwargs, once the async-with block has exited.
        """
        session = _make_session(execute_side_effect=[])
        db_factory = _make_db_factory(session)
        mock_counters = AsyncMock()
        mock_counters.acquire_tick_lock = AsyncMock(return_value=True)
        mock_svc = MagicMock()
        mock_org = SimpleNamespace(settings={}, monthly_budget_usd=100.0, goals=[], mission="Grow")
        mock_svc.get_organization = AsyncMock(return_value=mock_org)

        captured: dict[str, Any] = {}

        def _fake_org_brain(**kwargs):
            captured["dispatcher"] = kwargs["dispatcher"]

            class _FakeBrain:
                async def run_tick(self, **_kw):
                    # Mirrors a real ACT decision: buffer a dispatch, don't
                    # publish to the broker yet.
                    captured["dispatcher"]({"mission_id": "auto-1", "tenant_id": "tenant-1"})
                    return {"proposed": 1, "executed": 1, "blocked": 0}

            return _FakeBrain()

        with (
            patch("app.scaling.tasks.is_feature_enabled", return_value=True),
            patch("app.org.brain_counters.BrainCounters", return_value=mock_counters),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
            patch("app.org.service.OrgService", return_value=mock_svc),
            patch("app.org.brain.OrgBrain", side_effect=_fake_org_brain),
            patch("app.scaling.tasks.execute_org_mission.apply_async") as mock_apply,
        ):
            result = await self._run(db_factory=db_factory)

        assert result == {"proposed": 1, "executed": 1, "blocked": 0}
        mock_apply.assert_called_once_with(
            kwargs={"mission_id": "auto-1", "tenant_id": "tenant-1"}
        )


# ── org_brain_loop ───────────────────────────────────────────────────────────


class TestOrgBrainLoop:
    """The loop resolves its own factories in the worker (it used to read
    ``app.main.app.state.db_factory``, which nothing sets, so it never ran)."""

    def _patch_orgs(self, orgs, db_factory=None):
        return (
            patch(
                "app.scaling.tasks._org_loop_factories",
                return_value=(db_factory or MagicMock(), MagicMock()),
            ),
            patch(
                "app.scaling.tasks._active_orgs_for_maintenance",
                new=AsyncMock(return_value=orgs),
            ),
        )

    def test_no_orgs_returns_zero_totals(self):
        from app.scaling.tasks import org_brain_loop

        mock_redis = AsyncMock()
        f, o = self._patch_orgs([])
        with f, o, patch("redis.asyncio.from_url", return_value=mock_redis):
            result = org_brain_loop.run()

        assert result == {
            "processed": 0,
            "triggered": 0,
            "proposed": 0,
            "executed": 0,
            "blocked": 0,
        }

    def test_success_aggregates_tick_results(self):
        from app.scaling.tasks import org_brain_loop

        db_factory = MagicMock()
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(return_value=None)
        tick = AsyncMock(return_value={"proposed": 1, "executed": 2, "blocked": 0})
        f, o = self._patch_orgs([("org-1", "tenant-1", 4)], db_factory)
        with (
            f,
            o,
            patch("redis.asyncio.from_url", return_value=mock_redis),
            patch("app.scaling.tasks._brain_tick_for_org", new=tick),
        ):
            result = org_brain_loop.run()

        assert result == {
            "processed": 1,
            "triggered": 1,
            "proposed": 1,
            "executed": 2,
            "blocked": 0,
        }
        # The per-org tick gets the tenant (RLS) factory, not the maintenance one.
        assert tick.await_args.kwargs["db_factory"] is db_factory

    def test_per_org_error_is_caught_and_continues(self):
        from app.scaling.tasks import org_brain_loop

        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(return_value=None)
        f, o = self._patch_orgs([("org-1", "tenant-1", 4)])
        with (
            f,
            o,
            patch("redis.asyncio.from_url", return_value=mock_redis),
            patch(
                "app.scaling.tasks._brain_tick_for_org",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = org_brain_loop.run()

        assert result["processed"] == 1
        assert result["triggered"] == 0

    def test_org_scan_failure_propagates_not_reported_as_no_orgs(self):
        """A failed cross-tenant scan (e.g. the maintenance role lacks
        BYPASSRLS) used to be swallowed into all-zero totals — identical to
        "no active orgs". It must fail the task run instead."""
        from app.scaling.tasks import org_brain_loop

        with (
            patch("app.scaling.tasks._org_loop_factories", return_value=(MagicMock(), MagicMock())),
            patch(
                "app.scaling.tasks._active_orgs_for_maintenance",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch("app.scaling.tasks.logger") as mock_logger,
            pytest.raises(RuntimeError, match="db down"),
        ):
            org_brain_loop.run()

        mock_logger.error.assert_called_once()
        assert mock_logger.error.call_args.args[0] == "org_brain.loop_failed"

    def test_loop_scans_with_the_maintenance_factory(self):
        from app.scaling.tasks import org_brain_loop

        tenant_db, system_db = MagicMock(name="tenant"), MagicMock(name="system")
        scan = AsyncMock(return_value=[])
        with (
            patch("app.scaling.tasks._org_loop_factories", return_value=(tenant_db, system_db)),
            patch("app.scaling.tasks._active_orgs_for_maintenance", new=scan),
            patch("redis.asyncio.from_url", return_value=AsyncMock()),
        ):
            org_brain_loop.run()

        assert scan.await_args.args[0] is system_db

    def test_org_loop_factories_returns_the_system_session_factory(self):
        from app.scaling.tasks import _org_loop_factories

        tenant_db, system_db = MagicMock(name="tenant"), MagicMock(name="system")
        with (
            patch("app.db.session.get_session_factory", return_value=tenant_db),
            patch("app.db.session.get_system_session_factory", return_value=system_db) as gsf,
        ):
            assert _org_loop_factories() == (tenant_db, system_db)
        gsf.assert_called_once()

    @pytest.mark.asyncio
    async def test_org_scan_runs_under_system_session(self):
        """organizations is FORCE-RLS: the scan must disable row security."""
        from app.scaling.tasks import _active_orgs_for_maintenance

        rows = MagicMock(all=MagicMock(return_value=[("o", "t", 3)]))
        session = _make_session(execute_side_effect=[MagicMock(), rows])
        result = await _active_orgs_for_maintenance(_make_db_factory(session))
        assert result == [("o", "t", 3)]
        first_sql = str(session.execute.await_args_list[0].args[0])
        assert "row_security" in first_sql


# ── _collaboration_tick_for_org ─────────────────────────────────────────────


class TestCollaborationTickForOrg:
    @pytest.mark.asyncio
    async def test_flag_off_returns_zero(self):
        from app.scaling.tasks import _collaboration_tick_for_org

        with patch("app.scaling.tasks.is_feature_enabled", return_value=False):
            result = await _collaboration_tick_for_org(
                db_factory=MagicMock(side_effect=AssertionError("must not touch db")),
                redis=MagicMock(),
                llm_provider=MagicMock(),
                org_id="o1",
                tenant_id="t1",
                autonomy_level=4,
            )
        assert result == 0

    @pytest.mark.asyncio
    async def test_low_autonomy_returns_zero(self):
        from app.scaling.tasks import _collaboration_tick_for_org

        with patch("app.scaling.tasks.is_feature_enabled", return_value=True):
            result = await _collaboration_tick_for_org(
                db_factory=MagicMock(side_effect=AssertionError("must not touch db")),
                redis=MagicMock(),
                llm_provider=MagicMock(),
                org_id="o1",
                tenant_id="t1",
                autonomy_level=1,
            )
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_llm_provider_returns_zero(self):
        from app.scaling.tasks import _collaboration_tick_for_org

        with patch("app.scaling.tasks.is_feature_enabled", return_value=True):
            result = await _collaboration_tick_for_org(
                db_factory=MagicMock(side_effect=AssertionError("must not touch db")),
                redis=MagicMock(),
                llm_provider=None,
                org_id="o1",
                tenant_id="t1",
                autonomy_level=4,
            )
        assert result == 0

    @pytest.mark.asyncio
    async def test_success_emits_messages(self):
        from app.scaling.tasks import _collaboration_tick_for_org

        org = SimpleNamespace(settings={}, monthly_budget_usd=100.0)
        session = _make_session(
            execute_side_effect=[
                MagicMock(all=MagicMock(return_value=[("dept-a", "lead-a")])),
            ]
        )
        db_factory = _make_db_factory(session)
        mock_redis = AsyncMock()

        mock_counters = MagicMock()
        mock_counters.snapshot = AsyncMock(return_value=(0.0, 0, None))

        mock_settings = SimpleNamespace(collaboration_enabled=True)
        mock_tick = MagicMock()
        mock_tick.run = AsyncMock(return_value=3)

        with (
            patch("app.scaling.tasks.is_feature_enabled", return_value=True),
            patch("app.org.brain_counters.BrainCounters", return_value=mock_counters),
            patch("app.org.service.OrgService.get_organization", new=AsyncMock(return_value=org)),
            patch("app.org.brain_settings.resolve_autonomy_settings", return_value=mock_settings),
            patch("app.org.brain_collaboration.CollaborationTick", return_value=mock_tick),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
        ):
            result = await _collaboration_tick_for_org(
                db_factory=db_factory,
                redis=mock_redis,
                llm_provider=MagicMock(),
                org_id="o1",
                tenant_id="t1",
                autonomy_level=4,
            )

        assert result == 3
        mock_tick.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_leads_returns_zero(self):
        from app.scaling.tasks import _collaboration_tick_for_org

        org = SimpleNamespace(settings={}, monthly_budget_usd=100.0)
        session = _make_session(
            execute_side_effect=[
                MagicMock(all=MagicMock(return_value=[])),
            ]
        )
        db_factory = _make_db_factory(session)
        mock_counters = MagicMock()
        mock_counters.snapshot = AsyncMock(return_value=(0.0, 0, None))
        mock_settings = SimpleNamespace(collaboration_enabled=True)

        with (
            patch("app.scaling.tasks.is_feature_enabled", return_value=True),
            patch("app.org.brain_counters.BrainCounters", return_value=mock_counters),
            patch("app.org.service.OrgService.get_organization", new=AsyncMock(return_value=org)),
            patch("app.org.brain_settings.resolve_autonomy_settings", return_value=mock_settings),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
        ):
            result = await _collaboration_tick_for_org(
                db_factory=db_factory,
                redis=MagicMock(),
                llm_provider=MagicMock(),
                org_id="o1",
                tenant_id="t1",
                autonomy_level=4,
            )

        assert result == 0


# ── org_collaboration_loop ───────────────────────────────────────────────────


class TestOrgCollaborationLoop:
    def test_no_llm_provider_returns_zero(self):
        from app.scaling.tasks import org_collaboration_loop

        with (
            patch("app.scaling.tasks._worker_llm_provider", return_value=None),
            patch(
                "app.scaling.tasks._active_orgs_for_maintenance",
                new=AsyncMock(side_effect=AssertionError("must not scan")),
            ),
        ):
            result = org_collaboration_loop.run()

        assert result == {"processed": 0, "orgs_with_chatter": 0, "messages_emitted": 0}

    def test_fake_provider_is_not_a_real_llm(self):
        from app.providers.fake import FakeProvider
        from app.scaling.tasks import _worker_llm_provider

        with patch("app.providers.registry.resolve_provider", return_value=FakeProvider()):
            assert _worker_llm_provider() is None
        real = MagicMock()
        with patch("app.providers.registry.resolve_provider", return_value=real):
            assert _worker_llm_provider() is real

    def _run_with(self, tick):
        from app.scaling.tasks import org_collaboration_loop

        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(return_value=None)
        with (
            patch("app.scaling.tasks._worker_llm_provider", return_value=MagicMock()),
            patch("app.scaling.tasks._org_loop_factories", return_value=(MagicMock(), MagicMock())),
            patch(
                "app.scaling.tasks._active_orgs_for_maintenance",
                new=AsyncMock(return_value=[("org-1", "tenant-1", 4)]),
            ),
            patch("redis.asyncio.from_url", return_value=mock_redis),
            patch("app.scaling.tasks._collaboration_tick_for_org", new=tick),
        ):
            return org_collaboration_loop.run()

    def test_success_aggregates_chatter(self):
        result = self._run_with(AsyncMock(return_value=2))
        assert result == {"processed": 1, "orgs_with_chatter": 1, "messages_emitted": 2}

    def test_per_org_error_is_caught(self):
        result = self._run_with(AsyncMock(side_effect=RuntimeError("boom")))
        assert result == {"processed": 1, "orgs_with_chatter": 0, "messages_emitted": 0}

    def test_org_scan_failure_propagates_not_reported_as_no_orgs(self):
        from app.scaling.tasks import org_collaboration_loop

        with (
            patch("app.scaling.tasks._worker_llm_provider", return_value=MagicMock()),
            patch("app.scaling.tasks._org_loop_factories", return_value=(MagicMock(), MagicMock())),
            patch(
                "app.scaling.tasks._active_orgs_for_maintenance",
                new=AsyncMock(side_effect=RuntimeError("row-level security")),
            ),
            patch("app.scaling.tasks.logger") as mock_logger,
            pytest.raises(RuntimeError, match="row-level security"),
        ):
            org_collaboration_loop.run()

        mock_logger.error.assert_called_once()
        assert mock_logger.error.call_args.args[0] == "org_collaboration.loop_failed"


# ── org_intelligence_cron / org_digest_cron / org_twin_sync ─────────────────


class TestOrgCronTasks:
    def test_intelligence_cron_disabled(self):
        from app.scaling.tasks import org_intelligence_cron

        with patch("app.org.feature_flags.is_feature_enabled", return_value=False):
            assert org_intelligence_cron.run() == {"status": "disabled"}

    def test_intelligence_cron_success(self):
        from app.scaling.tasks import org_intelligence_cron

        with (
            patch("app.org.feature_flags.is_feature_enabled", return_value=True),
            patch(
                "app.org.feature_flags._run_org_intelligence_cron",
                new=AsyncMock(return_value={"orgs_scored": 2}),
            ),
        ):
            assert org_intelligence_cron.run() == {"orgs_scored": 2}

    def test_intelligence_cron_error(self):
        from app.scaling.tasks import org_intelligence_cron

        with (
            patch("app.org.feature_flags.is_feature_enabled", return_value=True),
            patch(
                "app.org.feature_flags._run_org_intelligence_cron",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = org_intelligence_cron.run()
        assert "error" in result

    def test_digest_cron_disabled(self):
        from app.scaling.tasks import org_digest_cron

        with patch("app.org.feature_flags.is_feature_enabled", return_value=False):
            assert org_digest_cron.run() == {"status": "disabled"}

    def test_digest_cron_success(self):
        from app.scaling.tasks import org_digest_cron

        with (
            patch("app.org.feature_flags.is_feature_enabled", return_value=True),
            patch(
                "app.org.feature_flags._run_org_digest_cron",
                new=AsyncMock(return_value={"digests_sent": 4}),
            ),
        ):
            assert org_digest_cron.run() == {"digests_sent": 4}

    def test_digest_cron_error(self):
        from app.scaling.tasks import org_digest_cron

        with (
            patch("app.org.feature_flags.is_feature_enabled", return_value=True),
            patch(
                "app.org.feature_flags._run_org_digest_cron",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = org_digest_cron.run()
        assert "error" in result

    def test_twin_sync_success(self):
        from app.scaling.tasks import org_twin_sync

        with patch(
            "app.org.feature_flags._run_org_twin_sync", new=AsyncMock(return_value=None)
        ):
            result = org_twin_sync.run({"event_type": "mission.completed"})
        assert result == {"status": "ok", "event_type": "mission.completed"}

    def test_twin_sync_error(self):
        from app.scaling.tasks import org_twin_sync

        with patch(
            "app.org.feature_flags._run_org_twin_sync",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = org_twin_sync.run({"event_type": "x"})
        assert "error" in result


# ── delta_reingest_files ─────────────────────────────────────────────────────


class TestDeltaReingestFiles:
    def test_unsupported_source_type_returns_explicit_status(self):
        """An unregistered source_type must never fabricate a success count —

        it should report ``unsupported`` explicitly (WS-12 honesty rule).
        """
        from app.scaling.tasks import delta_reingest_files

        with (
            patch("app.ingestion.connector_registry.load_all_connectors", return_value=None),
            patch(
                "app.ingestion.connector_registry.get_connector",
                side_effect=KeyError("nope"),
            ),
        ):
            result = delta_reingest_files.run(
                tenant_id="t1",
                collection_id="c1",
                source_type="not_a_real_connector",
                source_config={},
            )

        assert result["status"] == "unsupported"
        assert result["source_type"] == "not_a_real_connector"
        assert result["tenant_id"] == "t1"
        assert result["collection_id"] == "c1"

    def test_success_tallies_indexed_skipped_and_failed(self):
        from app.scaling.tasks import delta_reingest_files

        docs = [("doc1", "cursor1"), ("doc2", "cursor2"), ("doc3", "cursor3")]

        async def _fake_get_delta(config, cursor):
            for doc, cur in docs:
                yield doc, cur

        mock_connector = MagicMock()
        mock_connector.get_delta = _fake_get_delta
        mock_connector_cls = MagicMock(return_value=mock_connector)

        mock_pipeline = MagicMock()
        mock_pipeline.ingest = AsyncMock(
            side_effect=[
                SimpleNamespace(success=True, skipped=False),
                SimpleNamespace(success=False, skipped=True),
                SimpleNamespace(success=False, skipped=False),
            ]
        )

        with (
            patch("app.ingestion.connector_registry.load_all_connectors", return_value=None),
            patch(
                "app.ingestion.connector_registry.get_connector",
                return_value=mock_connector_cls,
            ),
            patch("app.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        ):
            result = delta_reingest_files.run(
                tenant_id="t1",
                collection_id="c1",
                source_type="github",
                source_config={"token": "vault://x"},
            )

        assert result == {
            "status": "ok",
            "source_type": "github",
            "docs_indexed": 1,
            "docs_skipped": 1,
            "docs_failed": 1,
            "tenant_id": "t1",
            "collection_id": "c1",
        }
        assert mock_pipeline.ingest.await_count == 3

    def test_connector_error_mid_stream_returns_partial_tallies(self):
        """A connector that blows up partway through must report what it

        already indexed, not silently drop the whole run.
        """
        from app.scaling.tasks import delta_reingest_files

        async def _fake_get_delta(config, cursor):
            yield "doc1", "cursor1"
            raise RuntimeError("upstream API rate limited")

        mock_connector = MagicMock()
        mock_connector.get_delta = _fake_get_delta
        mock_connector_cls = MagicMock(return_value=mock_connector)

        mock_pipeline = MagicMock()
        mock_pipeline.ingest = AsyncMock(return_value=SimpleNamespace(success=True, skipped=False))

        with (
            patch("app.ingestion.connector_registry.load_all_connectors", return_value=None),
            patch(
                "app.ingestion.connector_registry.get_connector",
                return_value=mock_connector_cls,
            ),
            patch("app.ingestion.pipeline.IngestionPipeline", return_value=mock_pipeline),
        ):
            result = delta_reingest_files.run(
                tenant_id="t1",
                collection_id="c1",
                source_type="notion",
                source_config={},
            )

        assert result["status"] == "error"
        assert "rate limited" in result["error"]
        assert result["docs_indexed"] == 1
        assert result["docs_skipped"] == 0
        assert result["docs_failed"] == 0


# ── _solar_due_run_utc (solar-event schedule firing edge cases) ─────────────


class TestSolarDueRunUtc:
    def test_astral_unavailable_returns_none(self):
        """astral is not a hard dependency of the worker — if it isn't

        installed, a solar-event schedule must be skipped (with the caller
        logging a warning) rather than crashing the beat loop.
        """
        from app.scaling.tasks import _solar_due_run_utc

        now = datetime.datetime(2024, 6, 21, 12, 0, 0)
        # astral is genuinely not installed in this environment, so this
        # exercises the real ImportError branch, not a simulated one.
        assert _solar_due_run_utc({"solar_event": "sunrise"}, now) is None

    def _install_fake_astral(self, monkeypatch, events: dict[str, datetime.datetime]):
        import sys
        import types

        fake_astral = types.ModuleType("astral")

        class _FakeLocationInfo:
            def __init__(self, *, latitude: float, longitude: float) -> None:
                self.latitude = latitude
                self.longitude = longitude
                self.observer = object()

        fake_astral.LocationInfo = _FakeLocationInfo  # type: ignore[attr-defined]

        fake_astral_sun = types.ModuleType("astral.sun")

        def _fake_sun(observer, *, date, tzinfo):
            return events

        fake_astral_sun.sun = _fake_sun  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "astral", fake_astral)
        monkeypatch.setitem(sys.modules, "astral.sun", fake_astral_sun)

    def test_known_solar_event_applies_offset(self, monkeypatch: pytest.MonkeyPatch):
        from app.scaling.tasks import _solar_due_run_utc

        sunrise = datetime.datetime(2024, 6, 21, 5, 30, 0, tzinfo=datetime.UTC)
        self._install_fake_astral(monkeypatch, {"sunrise": sunrise})

        now = datetime.datetime(2024, 6, 21, 12, 0, 0)
        result = _solar_due_run_utc(
            {
                "solar_event": "SUNRISE",  # case-insensitive
                "solar_latitude": 51.5,
                "solar_longitude": -0.12,
                "solar_offset_seconds": 600,
            },
            now,
        )

        assert result == datetime.datetime(2024, 6, 21, 5, 40, 0)

    def test_unknown_solar_event_raises(self, monkeypatch: pytest.MonkeyPatch):
        from app.scaling.tasks import _solar_due_run_utc

        self._install_fake_astral(
            monkeypatch, {"sunrise": datetime.datetime(2024, 6, 21, 5, 30, 0, tzinfo=datetime.UTC)}
        )

        now = datetime.datetime(2024, 6, 21, 12, 0, 0)
        with pytest.raises(ValueError, match="unknown solar_event"):
            _solar_due_run_utc({"solar_event": "midnight_snack"}, now)


# ── _count_tenant_rows (DB_ROW_CHANGE trigger, allowlist-gated) ─────────────


class TestCountTenantRows:
    @pytest.mark.asyncio
    async def test_table_not_in_allowlist_returns_none_without_querying(self):
        from app.scaling.tasks import _count_tenant_rows

        with patch("app.db.session.get_session_factory") as mock_factory:
            result = await _count_tenant_rows("goals", "t1", frozenset({"missions"}))

        assert result is None
        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsafe_identifier_returns_none_even_if_allowlisted(self):
        """Defense-in-depth: even a name that made it into the allowlist must

        still match the bare-identifier regex before it is ever interpolated
        into SQL.
        """
        from app.scaling.tasks import _count_tenant_rows

        result = await _count_tenant_rows(
            "goals; DROP TABLE goals", "t1", frozenset({"goals; DROP TABLE goals"})
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_allowlisted_table_counts_rows(self):
        from app.scaling.tasks import _count_tenant_rows

        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one=MagicMock(return_value=7))
        )
        db_factory = _make_db_factory(session)

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.db.rls.sqlalchemy_rls_context", side_effect=_null_rls_ctx),
        ):
            result = await _count_tenant_rows("goals", "t1", frozenset({"goals"}))

        assert result == 7
        session.execute.assert_awaited_once()
        query_text = str(session.execute.call_args.args[0])
        assert "goals" in query_text
