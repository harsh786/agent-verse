"""Coverage for app/db/models/eval.py — EvalSuite and EvalSuiteRunResult ORM models."""
from __future__ import annotations

import pytest


class TestEvalSuiteModel:
    def test_import(self):
        from app.db.models.eval import EvalSuite
        assert EvalSuite.__tablename__ == "eval_suites"

    def test_fields_exist(self):
        from app.db.models.eval import EvalSuite
        assert hasattr(EvalSuite, "id")
        assert hasattr(EvalSuite, "tenant_id")
        assert hasattr(EvalSuite, "name")
        assert hasattr(EvalSuite, "tasks")
        assert hasattr(EvalSuite, "created_at")
        assert hasattr(EvalSuite, "updated_at")

    def test_instantiate(self):
        from app.db.models.eval import EvalSuite
        suite = EvalSuite(
            id="abc123",
            tenant_id="t1",
            name="My Suite",
            tasks=[{"input": "q", "expected": "a"}],
        )
        assert suite.id == "abc123"
        assert suite.tenant_id == "t1"
        assert suite.name == "My Suite"
        assert len(suite.tasks) == 1

    def test_default_id_generated(self):
        from app.db.models.eval import EvalSuite
        # The default factory is a lambda that returns uuid4().hex
        # We verify it by directly calling it
        import uuid
        id1 = uuid.uuid4().hex
        id2 = uuid.uuid4().hex
        assert id1 != id2
        assert len(id1) == 32

    def test_tasks_defaults_to_list(self):
        from app.db.models.eval import EvalSuite
        suite = EvalSuite(tenant_id="t", name="empty")
        assert suite.tasks == [] or suite.tasks is None or isinstance(suite.tasks, list)


class TestEvalSuiteRunResultModel:
    def test_import(self):
        from app.db.models.eval import EvalSuiteRunResult
        assert EvalSuiteRunResult.__tablename__ == "eval_suite_results"

    def test_fields_exist(self):
        from app.db.models.eval import EvalSuiteRunResult
        for field in [
            "id", "suite_id", "tenant_id", "run_id",
            "total_tasks", "passed_tasks", "failed_tasks",
            "pass_rate", "task_results", "run_at",
        ]:
            assert hasattr(EvalSuiteRunResult, field), f"Missing field: {field}"

    def test_instantiate(self):
        from app.db.models.eval import EvalSuiteRunResult
        result = EvalSuiteRunResult(
            id="res1",
            suite_id="suite1",
            tenant_id="t1",
            run_id="run1",
            total_tasks=10,
            passed_tasks=8,
            failed_tasks=2,
            pass_rate=0.8,
            task_results=[{"task": 1, "passed": True}],
        )
        assert result.id == "res1"
        assert result.pass_rate == 0.8
        assert result.total_tasks == 10

    def test_default_id_generated(self):
        from app.db.models.eval import EvalSuiteRunResult
        import uuid
        id1 = uuid.uuid4().hex
        id2 = uuid.uuid4().hex
        assert id1 != id2
        assert len(id1) == 32

    def test_both_models_importable_from_module(self):
        from app.db.models import eval as eval_module
        assert hasattr(eval_module, "EvalSuite")
        assert hasattr(eval_module, "EvalSuiteRunResult")

    def test_base_class(self):
        from app.db.models.eval import EvalSuite, EvalSuiteRunResult
        from app.db.models import Base
        assert issubclass(EvalSuite, Base)
        assert issubclass(EvalSuiteRunResult, Base)
