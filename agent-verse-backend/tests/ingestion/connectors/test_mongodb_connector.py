"""Unit tests for MongoDBConnector's config handling, cursor and query shape.

The end-to-end behaviour (API -> validate -> sync against a real MongoDB,
replica-set member egress checks, TLS) lives in
tests/ingestion/test_mongodb_source_integration.py.
"""

from __future__ import annotations

import datetime
import sys
from typing import Any

import pytest
from bson import ObjectId

from app.ingestion.base_connector import ConnectorUnavailableError
from app.ingestion.connectors.mongodb_connector import (
    MongoDBConnector,
    _decode_cursor,
    _encode_cursor,
    _flatten_doc,
    _mongo_uri,
    _page_query,
    _parse_member,
    _settings,
    _single_host_uri,
)
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict[str, Any] | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-m",
        tenant_id="t1",
        name="Test Mongo",
        family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb",
        enabled=True,
        connection_config=conn_config
        or {"host": "mongo.test", "database": "db", "collection": "col"},
    )


class TestFlattenDoc:
    def test_flattens_nested_dict(self) -> None:
        text = _flatten_doc({"a": 1, "b": {"c": 2, "d": [1, 2, 3]}})
        assert "a: 1" in text
        assert "b.c: 2" in text
        assert "b.d[0]: 1" in text

    def test_respects_max_depth(self) -> None:
        doc = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too deep"}}}}}}}
        assert "too deep" not in _flatten_doc(doc, max_depth=2)


class TestSettings:
    def test_uri_or_host_port_never_a_localhost_default(self) -> None:
        assert _mongo_uri({"uri": "mongodb://a:1/"}) == "mongodb://a:1/"
        assert _mongo_uri({"host": "db.x", "port": 27018}) == "mongodb://db.x:27018/"
        assert _mongo_uri({"host": "h1:1, h2"}) == "mongodb://h1:1,h2:27017/"
        with pytest.raises(ValueError, match="URI or a host"):
            _mongo_uri({})

    def test_ui_collections_csv_is_read(self) -> None:
        s = _settings({"uri": "mongodb://h/", "database": "shop", "collections_csv": "a, b ,"})
        assert s.collections == ["a", "b"]
        assert _settings({"uri": "mongodb://h/", "database": "d", "collection": "c"}).collections == ["c"]
        assert _settings({"uri": "mongodb://h/", "database": "d", "collections": ["x"]}).collections == ["x"]
        assert _settings({"uri": "mongodb://h/", "database": "d"}).collections == []

    def test_database_falls_back_to_the_uri_path_and_is_required(self) -> None:
        assert _settings({"uri": "mongodb://h/shop"}).database == "shop"
        with pytest.raises(ValueError, match="database"):
            _settings({"uri": "mongodb://h/"})

    def test_credential_fields_are_driver_options_with_admin_auth_source(self) -> None:
        s = _settings(
            {"uri": "mongodb://h/shop", "username": "u", "password": "p@ss:/?#", "database": "shop"}
        )
        assert s.kwargs["username"] == "u"
        assert s.kwargs["password"] == "p@ss:/?#"
        assert s.kwargs["authSource"] == "admin"
        assert "p@ss" not in s.uri

    def test_explicit_auth_source_wins_and_uri_auth_source_is_kept(self) -> None:
        s = _settings({"uri": "mongodb://h/", "username": "u", "auth_source": "shop", "database": "d"})
        assert s.kwargs["authSource"] == "shop"
        s = _settings({"uri": "mongodb://h/?authSource=ops", "username": "u", "database": "d"})
        assert "authSource" not in s.kwargs  # the URI's own authSource applies

    @pytest.mark.parametrize(
        "option",
        [
            "tlsCAFile=/etc/ssl/ca.pem",
            "tlsCertificateKeyFile=/app/secrets/client.pem",
            "ssl_certfile=/x",
            "tlsCRLFile=/x",
            "proxyHost=10.0.0.5",
            "authMechanismProperties=ENVIRONMENT:azure",
        ],
    )
    def test_uri_options_touching_platform_files_or_proxies_are_refused(self, option: str) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            _settings({"uri": f"mongodb://h/?{option}", "database": "d"})

    @pytest.mark.parametrize("mechanism", ["MONGODB-AWS", "MONGODB-OIDC", "GSSAPI"])
    def test_ambient_credential_mechanisms_are_refused(self, mechanism: str) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            _settings({"uri": f"mongodb://h/?authMechanism={mechanism}", "database": "d"})
        with pytest.raises(ValueError, match="not allowed"):
            _settings({"uri": "mongodb://h/", "auth_mechanism": mechanism, "database": "d"})

    def test_x509_needs_a_client_certificate(self) -> None:
        with pytest.raises(ValueError, match="tls_client_cert"):
            _settings({"uri": "mongodb://h/", "auth_mechanism": "MONGODB-X509", "database": "d"})

    def test_client_cert_and_key_go_together(self) -> None:
        with pytest.raises(ValueError, match="together"):
            _settings({"uri": "mongodb://h/", "tls_client_cert": "C", "database": "d"})

    def test_tls_material_enables_tls(self) -> None:
        s = _settings({"uri": "mongodb://h/", "tls_ca_pem": "CA", "database": "d"})
        assert s.kwargs["tls"] is True
        assert "tlsCAFile" not in s.kwargs  # written to a temp file only while connected

    def test_direct_connection_and_load_balanced_skip_member_discovery(self) -> None:
        assert _settings({"uri": "mongodb://h/", "database": "d"}).discover_members
        assert not _settings({"uri": "mongodb://h/?directConnection=true", "database": "d"}).discover_members
        assert not _settings({"uri": "mongodb://h/?loadBalanced=true", "database": "d"}).discover_members
        assert not _settings(
            {"uri": "mongodb://h/", "database": "d", "direct_connection": True}
        ).discover_members

    def test_display_host_carries_no_credentials(self) -> None:
        s = _settings({"uri": "mongodb://u:secret@h1:1,h2:2/d"})
        assert s.display_host == "h1:1,h2:2"


def test_member_parsing_and_single_host_uri() -> None:
    assert _parse_member("Mongo-0.Example:27018") == ("mongo-0.example", 27018)
    assert _parse_member("m1") == ("m1", 27017)
    assert _parse_member("[::1]:27019") == ("::1", 27019)
    uri = _single_host_uri("mongodb://u:p@a:1,b:2/db?replicaSet=rs0&tls=true", "b", 2)
    assert uri == "mongodb://u:p@b:2/db?tls=true"


class TestCursor:
    def _s(self, **cc: Any) -> Any:
        return _settings({"uri": "mongodb://h/", "database": "d", **cc})

    def test_round_trips_bson_types(self) -> None:
        s = self._s(cursor_field="updated_at")
        oid = ObjectId()
        when = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
        positions = {"orders": {"value": when, "_id": oid}}
        decoded = _decode_cursor(_encode_cursor(s, positions), s, ["orders"])
        assert decoded["orders"]["_id"] == oid
        assert decoded["orders"]["value"].replace(tzinfo=datetime.UTC) == when

    def test_changed_cursor_field_restarts(self) -> None:
        old = self._s(cursor_field="updated_at")
        cursor = _encode_cursor(old, {"c": {"value": 1, "_id": 1}})
        assert _decode_cursor(cursor, self._s(), ["c"]) == {}

    def test_legacy_objectid_cursor_is_honoured_for_a_single_collection(self) -> None:
        oid = ObjectId()
        s = self._s()
        assert _decode_cursor(str(oid), s, ["c"]) == {"c": {"value": oid, "_id": oid}}
        assert _decode_cursor(str(oid), s, ["a", "b"]) == {}

    def test_unreadable_cursor_is_an_error_not_a_silent_resync(self) -> None:
        with pytest.raises(ValueError, match="cursor"):
            _decode_cursor("{not json", self._s(), ["c"])


class TestPageQuery:
    def test_id_cursor(self) -> None:
        oid = ObjectId()
        assert _page_query("_id", None) == {}
        assert _page_query("_id", {"value": oid, "_id": oid}) == {"_id": {"$gt": oid}}

    def test_custom_field_breaks_ties_on_id(self) -> None:
        oid = ObjectId()
        query = _page_query("updated_at", {"value": 5, "_id": oid})
        assert query["$and"][1] == {
            "$or": [{"updated_at": {"$gt": 5}}, {"updated_at": 5, "_id": {"$gt": oid}}]
        }


class TestMissingDriver:
    async def test_validate_reports_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "pymongo", None)
        health = await MongoDBConnector().validate_connection(_make_config())
        assert health.ok is False
        assert "pymongo" in health.error

    async def test_sync_fails_loudly_instead_of_yielding_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "pymongo", None)
        with pytest.raises(ConnectorUnavailableError, match="pymongo"):
            _ = [d async for d in MongoDBConnector().get_delta(_make_config(), None)]


async def test_config_errors_are_reported_by_validate() -> None:
    health = await MongoDBConnector().validate_connection(
        _make_config({"uri": "mongodb://h/?authMechanism=MONGODB-AWS", "database": "d"})
    )
    assert health.ok is False
    assert "not allowed" in health.error


def test_pymongo_is_a_core_dependency() -> None:
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).resolve().parents[3] / "pyproject.toml").read_text())
    core = [d.split(">")[0].split("=")[0].strip() for d in pyproject["project"]["dependencies"]]
    assert "pymongo" in core


def test_source_type_and_registration() -> None:
    from app.ingestion.connector_registry import get_connector

    assert MongoDBConnector().source_type == "mongodb"
    assert get_connector("mongodb") is MongoDBConnector


class TestStableDocIds:
    """TG-13: ids are Source + collection + _id, never the URI host."""

    def test_same_source_collection_and_id_is_the_same_document(self) -> None:
        from app.ingestion.connectors.mongodb_connector import _doc_id

        oid = ObjectId()
        a = _make_config({"uri": "mongodb://a.example/", "database": "db"})
        b = _make_config({"uri": "mongodb://b.example:27018/", "database": "db"})
        assert _doc_id(a, "orders", oid) == _doc_id(b, "orders", oid)
        assert _doc_id(a, "orders", oid) != _doc_id(a, "customers", oid)
        assert _doc_id(a, "orders", oid) != _doc_id(a, "orders", str(oid))  # types differ
        other = SourceConfig(**{**a.__dict__, "source_id": "src-other"})
        assert _doc_id(a, "orders", oid) != _doc_id(other, "orders", oid)
