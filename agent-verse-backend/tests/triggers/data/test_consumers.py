"""Tests for data consumers — DBRowChange, S3Event, APIPoller, RSSPoller."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from app.triggers.data.consumers import (
    DBRowChangeConsumer,
    S3EventConsumer,
    APIPoller,
    RSSPoller,
)
from app.triggers.store import ScheduleStore
from app.triggers.models import TriggerSpec, TriggerType


def make_store_and_tc(trigger_type, **kwargs):
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=trigger_type, **kwargs)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    return store, tc


# ── DBRowChangeConsumer ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_row_change_dispatches():
    store, _ = make_store_and_tc(TriggerType.DB_ROW_CHANGE)
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))
    consumer = DBRowChangeConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle_notification("users", "INSERT", {"id": 1}, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_db_row_change_filters_by_table():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.DB_ROW_CHANGE, db_table="orders")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    consumer = DBRowChangeConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    # Different table — should not dispatch
    await consumer.handle_notification("users", "INSERT", {}, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
    # Correct table — should dispatch
    await consumer.handle_notification("orders", "INSERT", {}, tenant_id="t1")
    mock_dispatcher.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_db_row_change_filters_by_operation():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.DB_ROW_CHANGE, db_operation="INSERT")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    consumer = DBRowChangeConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle_notification("users", "DELETE", {}, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
    await consumer.handle_notification("users", "INSERT", {}, tenant_id="t1")
    mock_dispatcher.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_db_row_change_no_store():
    consumer = DBRowChangeConsumer()
    result = await consumer.handle_notification("t", "I", {}, tenant_id="t1")
    assert result == []


# ── S3EventConsumer ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_s3_event_dispatches():
    store, _ = make_store_and_tc(TriggerType.S3_EVENT)
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))
    consumer = S3EventConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle("my-bucket", "data/file.json", "ObjectCreated", tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_s3_event_filters_by_bucket():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.S3_EVENT, s3_bucket="prod-bucket")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    consumer = S3EventConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle("other-bucket", "file.txt", "ObjectCreated", tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_s3_event_filters_by_prefix():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.S3_EVENT, s3_prefix="reports/")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))
    consumer = S3EventConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle("bucket", "data/file.csv", "ObjectCreated", tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
    await consumer.handle("bucket", "reports/q1.csv", "ObjectCreated", tenant_id="t1")
    mock_dispatcher.dispatch.assert_called_once()


# ── APIPoller ─────────────────────────────────────────────────────────────────

def test_api_poller_jsonpath_extract():
    poller = APIPoller()
    data = {"results": {"count": 42, "items": ["a", "b"]}}
    value = poller._extract_jsonpath(data, "$.results.count")
    assert value == 42


def test_api_poller_jsonpath_missing():
    poller = APIPoller()
    value = poller._extract_jsonpath({}, "$.missing.field")
    assert value is None


@pytest.mark.asyncio
async def test_api_poller_no_store():
    poller = APIPoller()
    result = await poller.poll_trigger({}, tenant_id="t1")
    assert result is None


# ── RSSPoller ─────────────────────────────────────────────────────────────────

def test_rss_parse_feed():
    poller = RSSPoller()
    xml = """
    <rss><channel>
    <item><title>Post 1</title><link>http://example.com/1</link><id>item-1</id></item>
    <item><title>Post 2</title><link>http://example.com/2</link><id>item-2</id></item>
    </channel></rss>
    """
    entries = poller._parse_feed(xml)
    assert len(entries) == 2
    assert entries[0]["title"] == "Post 1"
    assert entries[1]["link"] == "http://example.com/2"


def test_rss_dedup_seen_entries():
    poller = RSSPoller()
    xml = """<rss><channel>
    <item><title>Post 1</title><id>item-1</id></item>
    </channel></rss>"""
    entries1 = poller._parse_feed(xml)
    assert len(entries1) == 1
    # Simulate already-seen
    poller._seen_entries["trigger-1"] = {"item-1"}
    seen = poller._seen_entries["trigger-1"]
    entries2 = [e for e in entries1 if (e.get("id") or e.get("link", "")) not in seen]
    assert len(entries2) == 0
