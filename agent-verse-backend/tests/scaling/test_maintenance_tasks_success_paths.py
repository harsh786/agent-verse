"""Success/error-path coverage for the simple DB-backed maintenance tasks in
app/scaling/tasks.py that only had their "real SQL, not noop" shape asserted
elsewhere (tests/scaling/test_celery_maintenance_real.py), never their actual
execution paths:

 - enforce_hitl_sla, flush_audit_wal, scan_cost_anomalies
 - embed_marketplace_templates, conclude_stale_experiments, expire_stale_documents
 - process_dpdp_erasures
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _session(execute_side_effect):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock(return_value=None)
    return session


def _db_factory(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _session_with_begin(execute_side_effect):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    return session


# ── _do_check_email_goals ─────────────────────────────────────────────────────


class TestDoCheckEmailGoals:
    @pytest.mark.asyncio
    async def test_success_returns_processed_count(self):
        from app.scaling.tasks import _do_check_email_goals

        with (
            patch("app.db.session.get_session_factory", return_value=MagicMock()),
            patch("app.services.event_store.EventStore", return_value=MagicMock()),
            patch("app.services.goal_service.GoalService", return_value=MagicMock()),
            patch(
                "app.integrations.email.imap_listener.check_and_process_emails",
                new=AsyncMock(return_value=5),
            ),
        ):
            result = await _do_check_email_goals()
        assert result == {"status": "ok", "processed": 5}

    @pytest.mark.asyncio
    async def test_error_returns_error_status(self):
        from app.scaling.tasks import _do_check_email_goals

        with patch(
            "app.db.session.get_session_factory", side_effect=RuntimeError("no db")
        ):
            result = await _do_check_email_goals()
        assert result["status"] == "error"
        assert result["processed"] == 0


class TestCheckEmailGoalsTask:
    def test_disabled_by_default(self, monkeypatch):
        from app.scaling.tasks import check_email_goals

        monkeypatch.delenv("IMAP_ENABLED", raising=False)
        result = check_email_goals.run()
        assert result == {"status": "disabled", "processed": 0}

    def test_enabled_delegates_to_async_body(self, monkeypatch):
        from app.scaling.tasks import check_email_goals

        monkeypatch.setenv("IMAP_ENABLED", "true")
        with patch(
            "app.scaling.tasks._run_async", return_value={"status": "ok", "processed": 2}
        ):
            result = check_email_goals.run()
        assert result == {"status": "ok", "processed": 2}


# ── consolidate_memories_task ─────────────────────────────────────────────────


class TestConsolidateMemoriesTaskFullPath:
    def test_success_reports_both_counts(self, monkeypatch):
        from app.scaling.tasks import consolidate_memories_task

        monkeypatch.setenv("DATA_RETENTION_DAYS", "90")
        session = _session_with_begin(
            execute_side_effect=[
                MagicMock(rowcount=3),
                MagicMock(rowcount=7),
            ]
        )
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = consolidate_memories_task.run()
        assert result == {"duplicates_removed": 3, "expired_removed": 7}


# ── reindex_stale_knowledge / purge_expired_artifacts (success path) ─────────


class TestReindexStaleKnowledgeSuccess:
    def test_success_returns_marked_count(self):
        from app.scaling.tasks import reindex_stale_knowledge

        session = _session_with_begin(execute_side_effect=[MagicMock(rowcount=4)])
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = reindex_stale_knowledge.run()
        assert result == {"marked_for_reindex": 4}


class TestPurgeExpiredArtifactsSuccess:
    def test_success_returns_purged_count(self):
        from app.scaling.tasks import purge_expired_artifacts

        session = _session_with_begin(execute_side_effect=[MagicMock(rowcount=9)])
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = purge_expired_artifacts.run()
        assert result == {"purged_count": 9}


# ── run_gdpr_export ────────────────────────────────────────────────────────


class TestRunGdprExport:
    def test_success_marks_job_complete(self):
        from app.scaling.tasks import run_gdpr_export

        session = _session_with_begin(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[(1, "goal text", "completed", None)])),
                MagicMock(fetchall=MagicMock(return_value=[(1, 1, "tool_x", "ok")])),
                MagicMock(),
            ]
        )
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = run_gdpr_export.run(job_id="job-1", tenant_id="t1")
        assert result["status"] == "complete"
        assert result["job_id"] == "job-1"
        assert "download_url" in result

    def test_audit_query_failure_is_tolerated(self):
        from app.scaling.tasks import run_gdpr_export

        session = _session_with_begin(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[])),
                RuntimeError("audit_log missing"),
                MagicMock(),
            ]
        )
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = run_gdpr_export.run(job_id="job-2", tenant_id="t1")
        assert result["status"] == "complete"

    def test_failure_marks_job_failed_and_reraises(self):
        from app.scaling.tasks import run_gdpr_export

        session = _session_with_begin(
            execute_side_effect=[
                RuntimeError("db exploded"),
                MagicMock(),  # failure-path UPDATE gdpr_export_jobs
            ]
        )
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            with pytest.raises(RuntimeError, match="db exploded"):
                run_gdpr_export.run(job_id="job-3", tenant_id="t1")


# ── warm_jwks_cache ───────────────────────────────────────────────────────────


class TestWarmJwksCache:
    def test_success_returns_warmed_count(self):
        from app.scaling.tasks import warm_jwks_cache

        mock_redis_client = MagicMock()
        with (
            patch("app.db.session.get_session_factory", return_value=MagicMock()),
            patch(
                "app.auth.agent_identity._build_jwks",
                new=AsyncMock(return_value=[{"kid": "k1"}, {"kid": "k2"}]),
            ),
            patch("redis.from_url", return_value=mock_redis_client),
        ):
            result = warm_jwks_cache.run()
        assert result == {"warmed": 2}
        mock_redis_client.setex.assert_called_once()

    def test_error_returns_error_dict(self):
        from app.scaling.tasks import warm_jwks_cache

        with patch(
            "app.auth.agent_identity._build_jwks",
            new=AsyncMock(side_effect=RuntimeError("jwks build failed")),
        ):
            result = warm_jwks_cache.run()
        assert "error" in result


# ── enforce_hitl_sla ──────────────────────────────────────────────────────────


class TestEnforceHitlSla:
    def test_escalates_overdue_approvals(self):
        from app.scaling.tasks import enforce_hitl_sla

        session = _session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[("req-1", "t1", None), ("req-2", "t1", None)])),
                MagicMock(),
                MagicMock(),
            ]
        )
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = enforce_hitl_sla.run()
        assert result == {"enforced": 2}

    def test_no_overdue_returns_zero(self):
        from app.scaling.tasks import enforce_hitl_sla

        session = _session(execute_side_effect=[MagicMock(fetchall=MagicMock(return_value=[]))])
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = enforce_hitl_sla.run()
        assert result == {"enforced": 0}

    def test_db_error_returns_error_dict(self):
        from app.scaling.tasks import enforce_hitl_sla

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = enforce_hitl_sla.run()
        assert result == {"error": "no db", "enforced": 0}


# ── flush_audit_wal ───────────────────────────────────────────────────────────


class TestFlushAuditWal:
    def test_success_returns_flushed_count(self):
        from app.scaling.tasks import flush_audit_wal

        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(return_value=None)
        mock_flusher = MagicMock()
        mock_flusher.flush = AsyncMock(return_value=12)

        with (
            patch("redis.asyncio.from_url", return_value=mock_redis),
            patch("app.governance.audit_v3.AuditFlusher", return_value=mock_flusher),
        ):
            result = flush_audit_wal.run()
        assert result == {"flushed": 12}

    def test_error_returns_error_dict(self):
        from app.scaling.tasks import flush_audit_wal

        with patch("redis.asyncio.from_url", side_effect=RuntimeError("no redis")):
            result = flush_audit_wal.run()
        assert result == {"error": "no redis", "flushed": 0}


# ── scan_cost_anomalies ───────────────────────────────────────────────────────


class TestScanCostAnomalies:
    def test_success_scans_tenants(self):
        from app.scaling.tasks import scan_cost_anomalies

        mock_redis = AsyncMock()
        mock_redis.keys = AsyncMock(return_value=[b"cost:daily:t1:2024-01-01"])
        mock_redis.aclose = AsyncMock(return_value=None)
        mock_tracker = MagicMock()
        mock_tracker.detect_anomaly = AsyncMock(return_value=[{"metric": "spend"}])

        with (
            patch("redis.asyncio.from_url", return_value=mock_redis),
            patch("app.intelligence.cost_tracker.CostTracker", return_value=mock_tracker),
        ):
            result = scan_cost_anomalies.run()
        assert result == {"tenants_scanned": 1, "anomalies_found": 1}

    def test_per_tenant_error_is_swallowed(self):
        from app.scaling.tasks import scan_cost_anomalies

        mock_redis = AsyncMock()
        mock_redis.keys = AsyncMock(return_value=["cost:daily:t1:2024-01-01"])
        mock_redis.aclose = AsyncMock(return_value=None)
        mock_tracker = MagicMock()
        mock_tracker.detect_anomaly = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("redis.asyncio.from_url", return_value=mock_redis),
            patch("app.intelligence.cost_tracker.CostTracker", return_value=mock_tracker),
        ):
            result = scan_cost_anomalies.run()
        assert result == {"tenants_scanned": 1, "anomalies_found": 0}

    def test_error_returns_error_dict(self):
        from app.scaling.tasks import scan_cost_anomalies

        with patch("redis.asyncio.from_url", side_effect=RuntimeError("no redis")):
            result = scan_cost_anomalies.run()
        assert result == {"error": "no redis", "anomalies_found": 0}


# ── embed_marketplace_templates ───────────────────────────────────────────────


class TestEmbedMarketplaceTemplates:
    def test_success_returns_pending_count(self):
        from app.scaling.tasks import embed_marketplace_templates

        session = _session(execute_side_effect=[MagicMock(scalar=MagicMock(return_value=7))])
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = embed_marketplace_templates.run()
        assert result == {"status": "ok", "pending_embeddings": 7}

    def test_error_returns_error_status(self):
        from app.scaling.tasks import embed_marketplace_templates

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = embed_marketplace_templates.run()
        assert result == {"status": "error", "error": "no db"}


# ── conclude_stale_experiments ────────────────────────────────────────────────


class TestConcludeStaleExperiments:
    def test_success_returns_concluded_count(self):
        from app.scaling.tasks import conclude_stale_experiments

        session = _session(
            execute_side_effect=[MagicMock(fetchall=MagicMock(return_value=[(1,), (2,), (3,)]))]
        )
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = conclude_stale_experiments.run()
        assert result == {"status": "ok", "concluded": 3}

    def test_error_returns_error_status(self):
        from app.scaling.tasks import conclude_stale_experiments

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = conclude_stale_experiments.run()
        assert result == {"status": "error", "error": "no db"}


# ── expire_stale_documents ────────────────────────────────────────────────────


class TestExpireStaleDocuments:
    def test_success_returns_deleted_count(self):
        from app.scaling.tasks import expire_stale_documents

        session = _session(execute_side_effect=[MagicMock(fetchall=MagicMock(return_value=[(1,)]))])
        db_factory = _db_factory(session)
        with patch("app.db.session.get_session_factory", return_value=db_factory):
            result = expire_stale_documents.run()
        assert result == {"status": "ok", "deleted": 1}

    def test_error_returns_error_status(self):
        from app.scaling.tasks import expire_stale_documents

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = expire_stale_documents.run()
        assert result == {"status": "error", "error": "no db"}


# ── process_dpdp_erasures ─────────────────────────────────────────────────────


class TestProcessDpdpErasures:
    def test_no_db_returns_skipped(self):
        from app.scaling.tasks import process_dpdp_erasures

        with patch("app.db.session.get_session_factory", return_value=None):
            result = process_dpdp_erasures.run()
        assert result == {"status": "skipped", "reason": "no_db"}

    def test_success_processes_pending_requests(self):
        from app.scaling.tasks import process_dpdp_erasures

        session = _session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[("req-1", "t1", "dp-1")])),
                MagicMock(),
            ]
        )
        db_factory = _db_factory(session)
        mock_orchestrator = MagicMock()
        mock_orchestrator.execute_deletion = AsyncMock(
            return_value=SimpleNamespace(suspended=False, verified=True, residue={})
        )

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.governance.audit_v3.AuditV3", return_value=MagicMock()),
            patch(
                "app.lifecycle.deletion_orchestrator.DeletionOrchestrator",
                return_value=mock_orchestrator,
            ),
        ):
            result = process_dpdp_erasures.run()

        assert result["status"] == "ok"
        assert result["processed"] == 1
        mock_orchestrator.execute_deletion.assert_awaited_once_with("t1", "dp-1")
        # A clean, verified deletion is marked "completed".
        update_params = session.execute.call_args_list[1].args[1]
        assert update_params["st"] == "completed"

    def test_residue_from_concurrent_write_is_not_marked_completed(self):
        """Regression: a goal actively executing for the erased subject can write
        a new row into a store that already had its DELETE pass (execute_deletion
        cascades across several independent per-store transactions, so nothing
        locks out a concurrent writer). execute_deletion's own re-scan catches
        this as residue (verified=False), but the caller used to only branch on
        `suspended`, so a residue-positive run was still recorded "completed" --
        silently failing the erasure guarantee with no operator visibility."""
        from app.scaling.tasks import process_dpdp_erasures

        session = _session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[("req-1", "t1", "dp-1")])),
                MagicMock(),
            ]
        )
        db_factory = _db_factory(session)
        mock_orchestrator = MagicMock()
        mock_orchestrator.execute_deletion = AsyncMock(
            return_value=SimpleNamespace(
                suspended=False, verified=False, residue={"goal_feedback": 1}
            )
        )

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.governance.audit_v3.AuditV3", return_value=MagicMock()),
            patch(
                "app.lifecycle.deletion_orchestrator.DeletionOrchestrator",
                return_value=mock_orchestrator,
            ),
        ):
            result = process_dpdp_erasures.run()

        assert result["status"] == "ok"
        assert result["processed"] == 1
        update_params = session.execute.call_args_list[1].args[1]
        assert update_params["st"] != "completed"
        assert update_params["st"] == "verification_failed"

    def test_per_request_error_is_caught_and_skipped(self):
        from app.scaling.tasks import process_dpdp_erasures

        session = _session(
            execute_side_effect=[
                MagicMock(fetchall=MagicMock(return_value=[("req-1", "t1", "dp-1")])),
            ]
        )
        db_factory = _db_factory(session)
        mock_orchestrator = MagicMock()
        mock_orchestrator.execute_deletion = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("app.db.session.get_session_factory", return_value=db_factory),
            patch("app.governance.audit_v3.AuditV3", return_value=MagicMock()),
            patch(
                "app.lifecycle.deletion_orchestrator.DeletionOrchestrator",
                return_value=mock_orchestrator,
            ),
        ):
            result = process_dpdp_erasures.run()

        assert result["status"] == "ok"
        assert result["processed"] == 0
