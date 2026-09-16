"""Tests for MongoDBConnector — validate_connection, get_delta, cursor field
handling, and document flattening. pymongo/bson are not installed in the test
environment, so fake modules are injected into sys.modules for the success
paths (mirrors tests/knowledge/test_ingestors_coverage.py's pypdf pattern)."""
from __future__ import annotations

import sys
import types

import pytest

from app.ingestion.connectors.mongodb_connector import MongoDBConnector, _flatten_doc
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-m",
        tenant_id="t1",
        name="Test Mongo",
        family="nosql_database",
        source_type="mongodb",
        enabled=True,
        connection_config=conn_config or {"host": "localhost", "database": "db", "collection": "col"},
    )


class _FakeObjectId:
    def __init__(self, value="000000000000000000000000"):
        self.value = str(value)

    def __str__(self):
        return self.value

    def __repr__(self):
        return f"ObjectId({self.value!r})"


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs
        self.last_query = None

    def find(self, query):
        self.last_query = query
        return _FakeCursor(self._docs)


class _FakeDB(dict):
    def __getitem__(self, name):
        return self._col

    def __init__(self, col):
        super().__init__()
        self._col = col


class _FakeAdmin:
    def __init__(self, command_side_effect=None):
        self._side_effect = command_side_effect

    def command(self, cmd):
        if self._side_effect is not None:
            raise self._side_effect
        return {"ok": 1}


def _install_fake_pymongo(docs=None, ping_side_effect=None):
    docs = docs or []
    collection = _FakeCollection(docs)

    class FakeMongoClient:
        instances: list = []

        def __init__(self, *a, **kw):
            self.admin = _FakeAdmin(ping_side_effect)
            FakeMongoClient.instances.append(self)

        def __getitem__(self, name):
            return _FakeDB(collection)

    fake_pymongo = types.ModuleType("pymongo")
    fake_pymongo.MongoClient = FakeMongoClient
    fake_bson = types.ModuleType("bson")
    fake_bson.ObjectId = _FakeObjectId
    sys.modules["pymongo"] = fake_pymongo
    sys.modules["bson"] = fake_bson
    return collection, FakeMongoClient


@pytest.fixture(autouse=True)
def _clean_modules():
    saved_pymongo = sys.modules.get("pymongo")
    saved_bson = sys.modules.get("bson")
    yield
    for name, saved in (("pymongo", saved_pymongo), ("bson", saved_bson)):
        if saved is not None:
            sys.modules[name] = saved
        else:
            sys.modules.pop(name, None)


class TestFlattenDoc:
    def test_flattens_nested_dict(self):
        doc = {"a": 1, "b": {"c": 2, "d": [1, 2, 3]}}
        text = _flatten_doc(doc)
        assert "a: 1" in text
        assert "b.c: 2" in text
        assert "b.d[0]: 1" in text

    def test_respects_max_depth(self):
        # deeply nested — should not raise, just stop recursing past max_depth
        doc = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too deep"}}}}}}}
        text = _flatten_doc(doc, max_depth=2)
        assert isinstance(text, str)


class TestValidateConnection:
    async def test_no_library_installed(self):
        sys.modules.pop("pymongo", None)
        connector = MongoDBConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "pymongo" in health.error

    async def test_success(self):
        _install_fake_pymongo()
        connector = MongoDBConnector()
        health = await connector.validate_connection(
            _make_config({"host": "db.local", "database": "d", "collection": "c"})
        )
        assert health.ok is True
        assert health.metadata["host"] == "db.local"

    async def test_ping_failure(self):
        _install_fake_pymongo(ping_side_effect=ConnectionError("no route to host"))
        connector = MongoDBConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "no route to host" in health.error


class TestGetDelta:
    async def test_no_library_yields_nothing(self):
        sys.modules.pop("pymongo", None)
        sys.modules.pop("bson", None)
        connector = MongoDBConnector()
        docs = [d async for d in connector.get_delta(_make_config(), None)]
        assert docs == []

    async def test_yields_documents_with_default_cursor(self):
        docs_in = [
            {"_id": _FakeObjectId("aaa"), "name": "doc1", "value": 10},
            {"_id": _FakeObjectId("bbb"), "name": "doc2", "value": 20},
        ]
        _install_fake_pymongo(docs=docs_in)
        connector = MongoDBConnector()
        config = _make_config({"host": "h", "database": "d", "collection": "c", "batch_size": 10})
        results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 2
        doc0, cursor0 = results[0]
        assert "name: doc1" in doc0.content.decode()
        assert doc0.metadata["collection"] == "c"
        doc1, cursor1 = results[1]
        assert cursor1 == "bbb"  # advances to last doc's _id

    async def test_custom_cursor_field(self):
        docs_in = [{"_id": _FakeObjectId("x1"), "updated_at": "2026-02-01", "v": 1}]
        collection, _ = _install_fake_pymongo(docs=docs_in)
        connector = MongoDBConnector()
        config = _make_config(
            {
                "host": "h",
                "database": "d",
                "collection": "c",
                "cursor_field": "updated_at",
            }
        )
        results = [d async for d in connector.get_delta(config, "2026-01-01")]
        assert len(results) == 1
        # query used the custom cursor field, not _id
        assert collection.last_query == {"updated_at": {"$gt": "2026-01-01"}}

    async def test_default_id_cursor_query(self):
        collection, _ = _install_fake_pymongo(docs=[])
        connector = MongoDBConnector()
        config = _make_config({"host": "h", "database": "d", "collection": "c"})
        _ = [d async for d in connector.get_delta(config, "aaa")]
        assert "_id" in collection.last_query
        assert "$gt" in collection.last_query["_id"]


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert MongoDBConnector().source_type == "mongodb"
    assert get_connector("mongodb") is MongoDBConnector
