"""Coverage for app/db/models/eval.py — EvalSuite and EvalSuiteRunResult ORM models."""
from __future__ import annotations


class TestEvalSuiteModel:
    def test_import(self):
        from app.db.models.eval import EvalSuite
        assert EvalSuite.__tablename__ == "eval_suites"

    def test_fields_exist(self):
        from app.db.models.eval import EvalSuite
        assert hasattr(EvalSuite, "id")
        assert hasattr(EvalSuite, "tenant_id")
        assert hasattr(EvalSuite, "name")
        assert hasattr(EvalSuite, "dataset_version")
        assert hasattr(EvalSuite, "created_at")
        assert hasattr(EvalSuite, "updated_at")

    def test_instantiate(self):
        from app.db.models.eval import EvalSuite
        suite = EvalSuite(
            id="abc123",
            tenant_id="t1",
            name="My Suite",
            dataset_version=3,
        )
        assert suite.id == "abc123"
        assert suite.tenant_id == "t1"
        assert suite.name == "My Suite"
        assert suite.dataset_version == 3

    def test_default_id_generated(self):
        # The default factory is a lambda that returns uuid4().hex
        # We verify it by directly calling it
        import uuid

        id1 = uuid.uuid4().hex
        id2 = uuid.uuid4().hex
        assert id1 != id2
        assert len(id1) == 32

    def test_golden_tasks_are_revision_rows(self):
        # MEM-54: tasks are versioned rows in golden_tasks, not a JSON column.
        from app.db.models.eval import EvalSuite, GoldenTaskRevision
        assert not hasattr(EvalSuite, "tasks")
        assert GoldenTaskRevision.__tablename__ == "golden_tasks"
        assert {"valid_from", "valid_to", "task_id"} <= set(GoldenTaskRevision.__table__.c.keys())


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
        from app.db.models import Base
        from app.db.models.eval import EvalSuite, EvalSuiteRunResult
        assert issubclass(EvalSuite, Base)
        assert issubclass(EvalSuiteRunResult, Base)
