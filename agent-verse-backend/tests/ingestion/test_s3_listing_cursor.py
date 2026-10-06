"""P1b-3: the S3 / MinIO incremental cursor never skips an object.

ListObjectsV2 returns keys in lexicographic order, not by time. The old cursor
was the newest ``LastModified`` seen so far, committed every 100 documents — a
run cancelled (or a worker lost) part-way had already recorded a time newer than
objects it had not reached, and the next run skipped them forever. An object
changed during a run, at a time older than another object's, was never picked
up either. Live: 1,050 objects, sync cancelled after 250 → the resumed sync
indexed almost none of the remaining 800.
"""

from __future__ import annotations

import datetime as dt
import sys
from email.utils import format_datetime
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors.minio_connector import MinIOConnector
from app.ingestion.connectors.s3_connector import S3Connector, _ListingCursor
from app.ingestion.source_config import SourceConfig, SourceFamily

T0 = dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=dt.UTC)


class FakeS3:
    """A bucket with a server clock; pages of ``page_size`` keys in key order."""

    def __init__(self, page_size: int = 3) -> None:
        self.objects: dict[str, tuple[bytes, dt.datetime]] = {}
        self.now = T0
        self.page_size = page_size
        self.on_page: Any = None
        self.client_kwargs: list[dict[str, Any]] = []
        self.session_kwargs: list[dict[str, Any]] = []

    def put(self, key: str, body: bytes, at: dt.datetime | None = None) -> None:
        self.objects[key] = (body, at or self.now)

    # boto3 surface ------------------------------------------------------
    def head_bucket(self, Bucket: str) -> dict[str, Any]:  # noqa: N803
        return {}

    def list_objects_v2(self, **kw: Any) -> dict[str, Any]:
        return {"KeyCount": len(self.objects)}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        body, _ = self.objects[Key]
        return {"Body": MagicMock(read=lambda n=-1: body), "ContentType": "text/plain",
                "ContentLength": len(body)}

    def get_paginator(self, name: str) -> Any:
        fake = self

        class _Pag:
            def paginate(self, Bucket: str, Prefix: str = "", StartAfter: str = "") -> Any:  # noqa: N803
                keys = sorted(k for k in fake.objects if k.startswith(Prefix) and k > StartAfter)
                for i in range(0, max(len(keys), 1), fake.page_size):
                    if fake.on_page is not None:
                        fake.on_page(i)
                    chunk = keys[i:i + fake.page_size]
                    yield {
                        "Contents": [
                            {"Key": k, "LastModified": fake.objects[k][1],
                             "Size": len(fake.objects[k][0]), "ETag": '"e"'}
                            for k in chunk if k in fake.objects
                        ],
                        "ResponseMetadata": {"HTTPHeaders": {"date": format_datetime(
                            fake.now, usegmt=True)}},
                    }

        return _Pag()


def _boto(fake: FakeS3) -> dict[str, ModuleType]:
    mod = ModuleType("boto3")

    def _session(**kw: Any) -> Any:
        fake.session_kwargs.append(kw)
        sess = MagicMock()

        def _client(name: str, **ckw: Any) -> FakeS3:
            fake.client_kwargs.append(ckw)
            return fake

        sess.client.side_effect = _client
        return sess

    mod.Session = _session  # type: ignore[attr-defined]
    return {"boto3": mod}


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="s1", tenant_id="t1", name="s", family=SourceFamily.OBJECT_STORAGE,
        source_type="s3", connection_config={"bucket": "b", **cc},
    )


async def _run(fake: FakeS3, cursor: str | None, *, stop_after: int | None = None,
               connector: S3Connector | None = None, **cc: Any
               ) -> tuple[list[str], str | None, S3Connector]:
    conn = connector or S3Connector()
    keys: list[str] = []
    last: str | None = cursor
    with patch.dict(sys.modules, _boto(fake)):
        gen = conn.get_delta(_config(**cc), cursor)
        async for raw, cur in gen:
            keys.append(raw.metadata["s3_key"])  # doc ids are Source-scoped uuids now
            last = cur
            if stop_after is not None and len(keys) >= stop_after:
                await gen.aclose()
                break
    completed = getattr(conn, "completed_cursor", None)
    if stop_after is None and completed:
        last = completed
    return keys, last, conn


@pytest.mark.asyncio
async def test_a_cancelled_run_resumes_without_skipping_older_objects() -> None:
    fake = FakeS3(page_size=4)
    # Key order is unrelated to time: "k00" is the NEWEST object.
    for i in range(12):
        fake.put(f"k{i:02d}", b"x", T0 - dt.timedelta(minutes=i))
    fake.now = T0 + dt.timedelta(minutes=1)
    first, position, _ = await _run(fake, None, stop_after=5)
    assert first == [f"k{i:02d}" for i in range(5)]
    rest, _final, _ = await _run(fake, position)
    assert first + rest == [f"k{i:02d}" for i in range(12)]


@pytest.mark.asyncio
async def test_a_change_made_during_a_run_is_picked_up_next_run() -> None:
    fake = FakeS3(page_size=2)
    for k in ("a", "b", "c", "d"):
        fake.put(k, b"v1", T0 - dt.timedelta(hours=1))
    fake.now = T0

    def _during(i: int) -> None:
        if i == 2:  # "a" was already listed; it changes, then "d" changes later
            fake.put("a", b"v2", T0 + dt.timedelta(seconds=5))
            fake.put("d", b"v2", T0 + dt.timedelta(seconds=30))

    fake.on_page = _during
    _keys, cursor, _ = await _run(fake, None)
    fake.on_page = None
    fake.now = T0 + dt.timedelta(minutes=5)
    again, _, _ = await _run(fake, cursor)
    assert "a" in again, "the change to an already-listed object was skipped"


@pytest.mark.asyncio
async def test_unchanged_objects_are_not_listed_again() -> None:
    fake = FakeS3()
    for k in ("a", "b", "c"):
        fake.put(k, b"v1", T0 - dt.timedelta(hours=2))
    fake.now = T0
    _, cursor, _ = await _run(fake, None)
    fake.now = T0 + dt.timedelta(hours=1)
    fake.put("d", b"new", T0 + dt.timedelta(minutes=30))
    second, cursor2, _ = await _run(fake, cursor)
    assert second == ["d"]
    assert _ListingCursor.parse(cursor2).after == ""


@pytest.mark.asyncio
async def test_same_second_writes_at_the_watermark_are_kept() -> None:
    fake = FakeS3()
    fake.now = T0
    fake.put("a", b"1", T0 - dt.timedelta(hours=1))
    _, cursor, _ = await _run(fake, None, cursor_lookback_seconds=0)
    since = _ListingCursor.parse(cursor).since
    fake.put("b", b"2", dt.datetime.fromisoformat(since))  # exactly at the watermark
    second, _, _ = await _run(fake, cursor, cursor_lookback_seconds=0)
    assert second == ["b"]


@pytest.mark.asyncio
async def test_a_legacy_cursor_is_honoured() -> None:
    fake = FakeS3()
    fake.put("old", b"1", T0 - dt.timedelta(days=2))
    fake.put("new", b"2", T0 - dt.timedelta(hours=1))
    keys, _, _ = await _run(fake, (T0 - dt.timedelta(days=1)).isoformat())
    assert keys == ["new"]


@pytest.mark.asyncio
async def test_session_token_and_virtual_addressing_reach_boto3() -> None:
    fake = FakeS3()
    fake.put("a", b"1")
    await _run(fake, None, endpoint_url="http://example.com:9000", addressing_style="virtual",
               credentials={"access_key_id": "AK", "secret_access_key": "SK",
                            "session_token": "TOKEN"})
    assert fake.session_kwargs[0]["aws_session_token"] == "TOKEN"
    assert fake.session_kwargs[0]["region_name"] == "us-east-1"
    cfg = fake.client_kwargs[0]["config"]
    assert cfg.s3 == {"addressing_style": "virtual"}
    assert fake.client_kwargs[0]["endpoint_url"] == "http://example.com:9000"


@pytest.mark.asyncio
async def test_minio_defaults_to_path_style() -> None:
    fake = FakeS3()
    fake.put("a", b"1")
    await _run(fake, None, connector=MinIOConnector(), endpoint_url="http://example.com:9000")
    assert fake.client_kwargs[0]["config"].s3 == {"addressing_style": "path"}


def test_an_unknown_addressing_style_is_refused() -> None:
    with pytest.raises(ValueError, match="addressing_style"):
        S3Connector._client_kwargs(None, _config(addressing_style="dns"))


@pytest.mark.asyncio
async def test_flat_ui_credentials_are_used() -> None:
    """P1b-8: the UI form sent access_key_id / secret_access_key at the top level."""
    fake = FakeS3()
    fake.put("a", b"1")
    await _run(fake, None, access_key_id="AK", secret_access_key="SK")
    assert fake.session_kwargs[0]["aws_access_key_id"] == "AK"
    assert fake.session_kwargs[0]["aws_secret_access_key"] == "SK"


@pytest.mark.asyncio
async def test_validate_reports_the_s3_error_code() -> None:
    from botocore.exceptions import ClientError

    fake = FakeS3()

    def _denied(**kw: Any) -> dict[str, Any]:
        raise ClientError({"Error": {"Code": "SignatureDoesNotMatch", "Message": "bad key"},
                           "ResponseMetadata": {"HTTPStatusCode": 403}}, "ListObjectsV2")

    fake.list_objects_v2 = _denied  # type: ignore[method-assign]
    with patch.dict(sys.modules, _boto(fake)):
        health = await S3Connector().validate_connection(_config())
    assert not health.ok and "SignatureDoesNotMatch" in str(health.error)
