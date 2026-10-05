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
    _flatten,
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

    def test_nesting_past_max_depth_is_kept_serialised_and_marked(self) -> None:
        # TG-09: content below max_depth used to vanish silently.
        doc = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too deep"}}}}}}}
        text, truncation = _flatten(doc, max_depth=2)
        assert "too deep" in text
        assert "[nested deeper than 2 levels, as JSON]" in text
        assert truncation["deep_fields"] == 1


class TestBsonTypesAndLimits:
    """TG-09: BSON types render as readable values; arrays past the item limit
    are summarised with a marker and counted, never dropped silently."""

    def test_bson_types(self) -> None:
        import re

        from bson import Binary, Decimal128, Int64, Regex

        text, truncation = _flatten(
            {
                "price": Decimal128("1234.5600"),
                "blob": Binary(b"\x00\x01\x02secret-bytes", 0),
                "pattern": Regex("^ab+c$", "i"),
                "compiled": re.compile("x.y", re.MULTILINE),
                "big": Int64(9_007_199_254_740_993),
                "nothing": None,
                "when": datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
            }
        )
        assert "price: 1234.5600" in text
        assert "blob: <binary subtype 0, 15 bytes>" in text
        assert "secret-bytes" not in text  # raw bytes are not indexed
        assert "pattern: /^ab+c$/i" in text
        assert "compiled: /x.y/m" in text
        assert "big: 9007199254740993" in text
        assert "nothing: null" in text
        assert "when: 2026-01-02T03:04:05+00:00" in text
        assert truncation == {}

    def test_long_arrays_are_bounded_with_a_marker(self) -> None:
        text, truncation = _flatten({"tags": [f"t{i}" for i in range(250)]})
        assert "tags[0]: t0" in text
        assert "tags[99]: t99" in text
        assert "tags[100]" not in text
        assert "tags: … 150 more item(s) of 250 not indexed" in text
        assert truncation == {"array_items_omitted": 150, "arrays_truncated": 1}

    def test_arrays_of_21_to_100_items_are_now_kept(self) -> None:
        text, truncation = _flatten({"n": list(range(50))})
        assert "n[49]: 49" in text and truncation == {}

    async def test_truncation_is_recorded_on_the_document(self) -> None:
        import contextlib
        from unittest.mock import patch

        import app.ingestion.connectors.mongodb_connector as mc

        @contextlib.asynccontextmanager
        async def _connected(settings: Any) -> Any:
            yield object(), settings

        docs = [{"_id": 1, "tags": list(range(150))}]
        with (
            patch.object(mc, "_connected", _connected),
            patch.object(mc, "_fetch_page", side_effect=[docs, []]),
            patch.object(mc, "_change_stream_start", return_value=None),
        ):
            out = [d async for d, _c in MongoDBConnector().get_delta(_make_config(), None)]
        assert out[0].metadata["truncated"] == {"array_items_omitted": 50, "arrays_truncated": 1}


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
        # Version 8: distinguishable from the host-based (v5) ids of earlier releases.
        import uuid

        assert uuid.UUID(_doc_id(a, "orders", oid)).version == 8
        assert MongoDBConnector().manages_doc_id(_doc_id(a, "orders", oid))
        legacy = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mongodb://h:27017/db/orders/{oid}"))
        assert not MongoDBConnector().manages_doc_id(legacy)
        assert _doc_id(a, "orders", oid) != _doc_id(a, "customers", oid)
        assert _doc_id(a, "orders", oid) != _doc_id(a, "orders", str(oid))  # types differ
        other = SourceConfig(**{**a.__dict__, "source_id": "src-other"})
        assert _doc_id(a, "orders", oid) != _doc_id(other, "orders", oid)


class TestChangeStreamHistoryLost:
    async def test_a_lost_resume_point_re_reads_the_collection(self) -> None:
        """TG-07: when the stored change-stream token fell off the oplog the
        updates in between are unknown — the collection is read again."""
        import contextlib
        from unittest.mock import patch

        from bson import json_util

        import app.ingestion.connectors.mongodb_connector as mc

        docs = [{"_id": i, "v": i} for i in range(1, 4)]
        reads: list[Any] = []

        def _fetch(client: Any, s: Any, coll: str, position: Any, limit: int) -> list[Any]:
            after = (position or {}).get("_id")
            reads.append(after)
            return [d for d in docs if after is None or d["_id"] > after][:limit]

        @contextlib.asynccontextmanager
        async def _connected(settings: Any) -> Any:
            yield object(), settings

        cursor = json_util.dumps(
            {"v": 2, "field": "_id", "positions": {"col": {"value": 3, "_id": 3, "cs": {"t": 1}}}}
        )
        with (
            patch.object(mc, "_connected", _connected),
            patch.object(mc, "_fetch_page", _fetch),
            patch.object(mc, "_change_stream_start", return_value={"t": 2}),
            patch.object(mc, "_read_changes", return_value=([], None, True)),
        ):
            out = [
                d async for d in MongoDBConnector().get_delta(_make_config(), cursor)
            ]
        # Caught up (after _id 3) -> history lost -> re-read from the start.
        assert reads[0] == 3 and None in reads
        assert [d.metadata["_id"] for d, _c in out] == ["1", "2", "3"]
        final = json_util.loads(out[-1][1])["positions"]["col"]
        assert final["_id"] == 3 and final["cs"] == {"t": 2}
