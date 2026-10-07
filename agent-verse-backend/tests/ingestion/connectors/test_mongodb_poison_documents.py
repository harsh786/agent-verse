"""MONGO-FAIL-POISON (unit): poison documents never abort a MongoDB sync or vanish.

Live findings this covers:
1. one document with an invalid-UTF-8 string made pymongo raise ``InvalidBSON``
   for the whole page — the sync failed, every later document was never read
   and nothing reached the DLQ;
2. a document with control characters (and odd BSON types) flattened to text
   holding NUL bytes, which the parser refused as "unsupported binary content";
3. over-size / deeply nested / huge-array documents are bounded with a note or
   dead-lettered with a reason — never silently lost.

The container test (real MongoDB replica set + Postgres DLQ + real pipeline) is
``tests/ingestion/test_mongodb_poison_documents_integration.py``.
"""

from __future__ import annotations

import contextlib
import datetime
import struct
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import bson
import pytest
from bson import (
    Binary,
    Code,
    Decimal128,
    MaxKey,
    MinKey,
    ObjectId,
    Regex,
    Timestamp,
    json_util,
)
from bson.codec_options import DEFAULT_CODEC_OPTIONS, CodecOptions
from bson.datetime_ms import DatetimeMS
from bson.dbref import DBRef
from bson.raw_bson import RawBSONDocument

import app.ingestion.connectors.mongodb_connector as mc
from app.ingestion.connectors.mongodb_connector import (
    MongoDBConnector,
    _decode_item,
    _decode_options,
    _fetch_page,
    _flatten,
    _raw_document,
    _read_changes,
    _render,
    _settings,
    _Undecodable,
)
from app.ingestion.parser_registry import _looks_binary
from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    CONNECTOR_FAILURE_RETRYABLE_KEY,
    CONNECTOR_REPLAY_KEY,
    SourceConfig,
)


def _string_el(name: str, value: bytes) -> bytes:
    return b"\x02" + name.encode() + b"\x00" + struct.pack("<i", len(value) + 1) + value + b"\x00"


def _invalid_utf8(key: str, *, id_bytes: bytes | None = None) -> bytes:
    """A BSON document whose ``notes`` holds invalid UTF-8 (as commerce_seed plants it)."""
    body = (
        _string_el("_id", id_bytes if id_bytes is not None else key.encode())
        + _string_el("kind", b"invalid_utf8")
        + _string_el("notes", b"Merchant note with a broken byte \xc3\x28 from a legacy export")
    )
    return struct.pack("<i", len(body) + 5) + body + b"\x00"


def _raw(doc: dict[str, Any] | bytes) -> RawBSONDocument:
    return RawBSONDocument(doc if isinstance(doc, bytes) else bson.encode(doc))


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-poison",
        tenant_id="t1",
        name="poison",
        family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb",
        enabled=True,
        connection_config={"host": "mongo.test", "database": "shop", "collection": "poison",
                           **cc},
    )


class _Collection:
    """Just enough of a pymongo Collection for ``_fetch_page`` / ``_fetch_one``."""

    def __init__(self, raws: list[RawBSONDocument]) -> None:
        self.raws = raws
        self.codec_options = DEFAULT_CODEC_OPTIONS
        self.queries: list[dict[str, Any]] = []

    def with_options(self, *, codec_options: CodecOptions[Any]) -> _Collection:
        assert codec_options.document_class is RawBSONDocument  # pages arrive undecoded
        return self

    def find(self, query: dict[str, Any], **_kw: Any) -> list[RawBSONDocument]:
        self.queries.append(query)
        return list(self.raws)

    def find_one(self, query: dict[str, Any], **_kw: Any) -> RawBSONDocument | None:
        for raw in self.raws:
            if bson.decode(raw.raw, codec_options=CodecOptions(
                    unicode_decode_error_handler="replace"))["_id"] == query["_id"]:
                return raw
        return None


def _client(col: _Collection) -> Any:
    return {"shop": {"poison": col}}


# ── 1. decoding isolation ────────────────────────────────────────────────────


class TestDecodingIsolation:
    def test_plain_pymongo_fails_the_whole_batch(self) -> None:
        """The root cause: decoding the batch as dicts raises for every document."""
        from bson.errors import InvalidBSON

        batch = bson.encode({"_id": "OK-1"}) + _invalid_utf8("BAD") + bson.encode({"_id": "OK-2"})
        with pytest.raises(InvalidBSON):
            bson.decode_all(batch)

    def test_one_bad_document_does_not_fail_its_page(self) -> None:
        col = _Collection([_raw({"_id": "OK-1", "n": 1}), _raw(_invalid_utf8("BAD")),
                           _raw({"_id": "OK-2", "n": 2})])
        settings = _settings({"uri": "mongodb://h/", "database": "shop"})
        page = _fetch_page(_client(col), settings, "poison", None, 10)
        assert page[0] == {"_id": "OK-1", "n": 1}
        assert page[2] == {"_id": "OK-2", "n": 2}
        bad = page[1]
        assert isinstance(bad, _Undecodable)
        assert bad.oid == "BAD" and bad.value == "BAD"
        assert "utf-8" in str(bad.error).lower()

    def test_custom_cursor_field_value_is_read_leniently(self) -> None:
        body = _invalid_utf8("BAD")[4:-1] + b"\x10seq\x00" + struct.pack("<i", 7)
        raw = RawBSONDocument(struct.pack("<i", len(body) + 5) + body + b"\x00")
        item = _decode_item(raw, _decode_options(_Collection([])), "seq")
        assert isinstance(item, _Undecodable) and (item.oid, item.value) == ("BAD", 7)

    def test_an_undecodable_id_fails_honestly_instead_of_skipping_documents(self) -> None:
        """A replaced ``_id`` cannot position ``$gt`` exactly: never guess."""
        from bson.errors import InvalidBSON

        raw = RawBSONDocument(_invalid_utf8("x", id_bytes=b"ID-\xc3\x28"))
        with pytest.raises(InvalidBSON):
            _decode_item(raw, _decode_options(_Collection([])), "_id")

    def test_dates_outside_pythons_range_decode(self) -> None:
        """A year-0 / year-10000+ date used to raise InvalidBSON for the page too."""
        raw = _raw({"_id": 1, "when": DatetimeMS(2**60)})
        item = _decode_item(raw, _decode_options(_Collection([])), "_id")
        assert isinstance(item, dict)
        text, _t = _flatten(item)
        assert "when: <datetime 1152921504606846976 ms since the epoch" in text

    async def test_sync_dead_letters_the_bad_document_and_continues(self) -> None:
        @contextlib.asynccontextmanager
        async def _connected(settings: Any) -> Any:
            yield _client(col), settings

        col = _Collection([_raw({"_id": "A-1", "notes": "first"}), _raw(_invalid_utf8("B-BAD")),
                           _raw({"_id": "C-2", "notes": "after the poison"})])
        with (
            patch.object(mc, "_connected", _connected),
            patch.object(mc, "_change_stream_start", return_value=None),
            patch.object(mc, "_existing_collections", lambda _c, _s, names: set(names)),
        ):
            out = [d async for d in MongoDBConnector().get_delta(_config(batch_size=10), None)]

        assert [d.metadata["_id"] for d, _c in out] == ["A-1", "B-BAD", "C-2"]
        good, bad, after = (d for d, _c in out)
        assert CONNECTOR_FAILURE_KEY not in good.metadata
        assert b"notes: after the poison" in after.content  # valid documents never lost
        reason = bad.metadata[CONNECTOR_FAILURE_KEY]
        assert "_id=B-BAD in shop.poison" in reason
        assert "not valid UTF-8" in reason and "(error id " in reason
        assert "\\xc3" not in reason and "codec can't decode" not in reason  # MDB-20
        assert bad.metadata[CONNECTOR_FAILURE_RETRYABLE_KEY] is True
        assert bad.content == b""
        # Same id as the document gets once fixed: a later index resolves the DLQ entry.
        assert bad.doc_id == mc._doc_id(_config(), "poison", "B-BAD")
        replay = bad.metadata[CONNECTOR_REPLAY_KEY]
        assert replay["kind"] == "mongodb_document" and replay["collection"] == "poison"
        assert json_util.loads(replay["id"]) == {"_id": "B-BAD"}
        # The cursor moves past the poison document (a re-sync never loops on it).
        positions = [json_util.loads(c)["positions"]["poison"]["_id"] for _d, c in out]
        assert positions == ["A-1", "B-BAD", "C-2"]

    async def test_the_real_pipeline_fails_it_with_the_reason(self) -> None:
        from app.ingestion.pipeline import IngestionPipeline

        item = _decode_item(_raw(_invalid_utf8("B-BAD")), _decode_options(_Collection([])), "_id")
        settings = _settings(_config().connection_config)
        doc = mc._document_for(_config(), settings, "poison", item)
        result = await IngestionPipeline(dry_run=True).ingest(doc, _config())
        assert result.status == "failed"
        assert result.error.startswith("connector: document _id=B-BAD")
        assert result.error.endswith("(retryable)")


class TestChangeStream:
    def test_an_undecodable_full_document_is_an_item_not_a_failed_read(self) -> None:
        opts = CodecOptions(document_class=RawBSONDocument)
        event = RawBSONDocument(
            bson.encode({"_id": {"_data": "82AA"}, "operationType": "replace",
                         "fullDocument": RawBSONDocument(_invalid_utf8("B-BAD"))}),
            codec_options=opts,
        )
        good = RawBSONDocument(
            bson.encode({"_id": {"_data": "82AB"}, "operationType": "update",
                         "fullDocument": {"_id": "A-1", "n": 2}}),
            codec_options=opts,
        )
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.try_next.side_effect = [event, good, None]
        stream.resume_token = RawBSONDocument(bson.encode({"_data": "82AB"}))
        col = MagicMock()
        col.codec_options = DEFAULT_CODEC_OPTIONS
        col.with_options.return_value = col
        col.watch.return_value = stream
        settings = _settings({"uri": "mongodb://h/", "database": "shop"})
        changes, token, lost = _read_changes({"shop": {"c": col}}, settings, "c", {"t": 1}, 10)
        assert lost is False and token == {"_data": "82AB"}  # tokens stored as plain dicts
        (bad, bad_token), (doc, _t) = changes
        assert isinstance(bad, _Undecodable) and bad.oid == "B-BAD"
        assert bad_token == {"_data": "82AB"}
        assert doc == {"_id": "A-1", "n": 2}


# ── 2. odd BSON types render as safe text ────────────────────────────────────


_ODD = {
    "_id": "POISON-ODD",
    "notes": "Control characters \x00\x01\x07 and lone ​ zero-width spaces in a note.",
    "empty": {},
    "empty_list": [],
    "neg_zero": -0.0,
    "nan": float("nan"),
    "blob": Binary(b"\x00\x01\x02\xffsecret", 0),
    "uuid4": Binary(uuid.UUID("12345678-1234-5678-1234-567812345678").bytes, 4),
    "uuid3": Binary(b"\x01" * 16, 3),
    "pattern": Regex("^ab+c$", "im"),
    "ts": Timestamp(1_700_000_000, 7),
    "low": MinKey(),
    "high": MaxKey(),
    "js": Code("function () { return 1; }"),
    "js_scope": Code("x + y", {"x": 1, "y": 2}),
    "price": Decimal128("1234.5600"),
    "price_nan": Decimal128("NaN"),
    "ref": DBRef("merchants", ObjectId("64f000000000000000000001"), "shop"),
    "far_future": DatetimeMS(2**60),
    "\x01key": "a key with a control character",
}


class TestOddBsonTypes:
    def _rendered(self) -> tuple[str, dict[str, int], dict[str, int]]:
        # Round-trip through real BSON so every value is what pymongo decodes.
        raw = _raw(_ODD)
        item = _decode_item(raw, _decode_options(_Collection([])), "_id")
        assert isinstance(item, dict)
        return _render(item)

    def test_every_value_renders_as_text(self) -> None:
        text, truncation, notes = self._rendered()
        assert "notes: Control characters \\u0000\\u0001\\u0007 and lone ​ zero-width" in text
        assert "empty: {}" in text and "empty_list: []" in text
        assert "neg_zero: -0.0" in text and "nan: nan" in text
        assert "blob: <binary 10 bytes>" in text and "secret" not in text  # subtype 0 = bytes
        assert "uuid4: <uuid 12345678-1234-5678-1234-567812345678>" in text
        assert "uuid3: <legacy uuid (binary subtype 3), hex 0101" in text
        assert "pattern: /^ab+c$/im" in text
        assert "ts: 2023-11-14T22:13:20+00:00 (BSON timestamp, increment 7)" in text
        assert "low: <MinKey>" in text and "high: <MaxKey>" in text
        assert "js: <javascript code> function () { return 1; }" in text
        assert 'js_scope: <javascript code> x + y (scope: {"x": 1, "y": 2})' in text
        assert "price: 1234.5600" in text and "price_nan: NaN" in text
        assert ('ref: {"$ref": "merchants", "$id": "64f000000000000000000001", '
                '"$db": "shop"}') in text
        assert "far_future: <datetime 1152921504606846976 ms since the epoch" in text
        assert "\\u0001key: a key with a control character" in text
        assert truncation == {}
        assert notes == {"control_chars_escaped": 4}

    def test_the_text_is_never_sniffed_as_binary(self) -> None:
        text, _t, _n = self._rendered()
        content = text.encode()
        assert b"\x00" not in content and not _looks_binary(content)
        assert not any(c < 0x20 and c not in (0x09, 0x0A, 0x0D) for c in content)

    def test_the_document_records_the_escapes(self) -> None:
        settings = _settings(_config().connection_config)
        doc = _raw_document(_config(), settings, "poison", dict(_ODD))
        assert doc.content_type == "text/plain"
        assert doc.metadata["rendering"] == {"control_chars_escaped": 4}
        assert "truncated" not in doc.metadata

    async def test_the_real_parser_accepts_it(self) -> None:
        """It used to fail: 'unsupported binary content (no extractor for this file type)'."""
        from app.ingestion.pipeline import IngestionPipeline

        settings = _settings(_config().connection_config)
        doc = _raw_document(_config(), settings, "poison", dict(_ODD))
        result = await IngestionPipeline(dry_run=True).ingest(doc, _config())
        assert result.status != "failed", result.error
        assert "binary" not in str(result.error or "")


# ── 3. caps: over-size, deep nesting, huge arrays ────────────────────────────


def _nested(depth: int, leaf: str) -> dict[str, Any]:
    doc: dict[str, Any] = {"leaf": leaf}
    for k in range(depth - 1):
        doc = {f"l{depth - 1 - k}": doc}
    return doc


class TestCaps:
    def test_95_levels_are_bounded_with_a_note_and_the_leaf_kept(self) -> None:
        settings = _settings(_config().connection_config)
        doc = _raw_document(_config(), settings, "poison", {
            "_id": "POISON-DEEP", "tree": _nested(95, "Bottom of the 95-level settlement tree")})
        text = doc.content.decode()
        assert "[nested deeper than 5 levels, as JSON]" in text
        assert "Bottom of the 95-level settlement tree" in text
        assert doc.metadata["truncated"] == {"deep_fields": 1}

    def test_a_20000_item_array_is_capped_with_a_note(self) -> None:
        settings = _settings(_config().connection_config)
        doc = _raw_document(_config(), settings, "poison", {
            "_id": "POISON-ARRAY",
            "events": [{"seq": k, "page": f"/p/{k % 97}"} for k in range(20000)]})
        text = doc.content.decode()
        assert "events[99].seq: 99" in text and "events[100]" not in text
        assert "events: … 19900 more item(s) of 20000 not indexed" in text
        assert doc.metadata["truncated"] == {"arrays_truncated": 1, "array_items_omitted": 19900}

    async def test_an_over_size_document_is_dead_lettered_with_a_reason(self) -> None:
        @contextlib.asynccontextmanager
        async def _connected(settings: Any) -> Any:
            yield _client(col), settings

        col = _Collection([_raw({"_id": "BIG", "blob": "x" * 5000}), _raw({"_id": "SMALL"})])
        config = _config()
        config.max_doc_size_bytes = 4096
        with (
            patch.object(mc, "_connected", _connected),
            patch.object(mc, "_change_stream_start", return_value=None),
            patch.object(mc, "_existing_collections", lambda _c, _s, names: set(names)),
        ):
            out = [d async for d, _c in MongoDBConnector().get_delta(config, None)]
        big, small = out
        reason = big.metadata[CONNECTOR_FAILURE_KEY]
        assert "_id=BIG in shop.poison is 5015 bytes as text" in reason
        assert "over this Source's 4096-byte per-document cap" in reason
        assert big.content == b"" and big.metadata[CONNECTOR_REPLAY_KEY]["collection"] == "poison"
        assert CONNECTOR_FAILURE_KEY not in small.metadata


# ── DLQ replay (operator / automatic retry) ──────────────────────────────────


class TestReplay:
    def _replay_ref(self, key: Any) -> dict[str, Any]:
        return {"kind": "mongodb_document", "collection": "poison",
                "id": json_util.dumps({"_id": key},
                                      json_options=json_util.CANONICAL_JSON_OPTIONS)}

    async def _replay(self, col: _Collection, ref: dict[str, Any]) -> list[Any]:
        @contextlib.asynccontextmanager
        async def _connected(settings: Any) -> Any:
            yield _client(col), settings

        with patch.object(mc, "_connected", _connected):
            return [d async for d in MongoDBConnector().replay_event(_config(), ref)]

    async def test_a_fixed_document_is_read_again_as_the_sync_indexes_it(self) -> None:
        oid = ObjectId("64f000000000000000000002")
        col = _Collection([_raw({"_id": oid, "notes": "re-exported as valid UTF-8"})])
        (doc,) = await self._replay(col, self._replay_ref(oid))
        assert CONNECTOR_FAILURE_KEY not in doc.metadata
        assert b"notes: re-exported as valid UTF-8" in doc.content
        assert doc.doc_id == mc._doc_id(_config(), "poison", oid)

    async def test_a_still_broken_document_is_a_fresh_failure(self) -> None:
        (doc,) = await self._replay(_Collection([_raw(_invalid_utf8("B-BAD"))]),
                                    self._replay_ref("B-BAD"))
        assert "not valid UTF-8" in doc.metadata[CONNECTOR_FAILURE_KEY]
        assert doc.metadata[CONNECTOR_FAILURE_RETRYABLE_KEY] is True

    async def test_a_document_gone_upstream_is_permanent(self) -> None:
        (doc,) = await self._replay(_Collection([]), self._replay_ref("GONE"))
        assert "no longer exists in shop.poison" in doc.metadata[CONNECTOR_FAILURE_KEY]
        assert doc.metadata[CONNECTOR_FAILURE_RETRYABLE_KEY] is False

    async def test_a_foreign_reference_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a MongoDB document replay reference"):
            await self._replay(_Collection([]), {"kind": "url", "url": "https://x"})


def test_timestamps_render_in_utc() -> None:
    text, _t = _flatten({"ts": Timestamp(0, 1)})
    assert text == "ts: " + datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC).isoformat() + (
        " (BSON timestamp, increment 1)")
