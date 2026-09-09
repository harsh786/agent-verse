"""Tests for advanced consumers — GraphQL subscriptions, WebSocket messages, price thresholds."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.triggers.advanced.consumers import (
    GraphQLSubscriptionConsumer,
    PriceThresholdPoller,
    WebSocketMessageConsumer,
)
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore


def make_store(trigger_type, **kwargs):
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=trigger_type, **kwargs)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    return store


# ── GraphQLSubscriptionConsumer ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_graphql_sub_dispatches():
    store = make_store(TriggerType.GRAPHQL_SUBSCRIPTION)
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    consumer = GraphQLSubscriptionConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle_message("wss://api.example.com/graphql", {"event": "new_order"}, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_graphql_sub_filters_endpoint():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.GRAPHQL_SUBSCRIPTION, graphql_endpoint="wss://api.example.com/graphql")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    consumer = GraphQLSubscriptionConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    # Different endpoint
    await consumer.handle_message("wss://other.com/graphql", {}, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
    # Correct endpoint
    await consumer.handle_message("wss://api.example.com/graphql", {}, tenant_id="t1")
    mock_dispatcher.dispatch.assert_called_once()


# ── WebSocketMessageConsumer ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_websocket_dispatches():
    store = make_store(TriggerType.WEBSOCKET_MESSAGE)
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    consumer = WebSocketMessageConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle_message("wss://stream.example.com", "data: {}", tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_websocket_pattern_filter():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.WEBSOCKET_MESSAGE, websocket_message_pattern=r"alert:\w+")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    consumer = WebSocketMessageConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    # No match
    await consumer.handle_message("wss://s.com", "info: nothing", tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
    # Match
    await consumer.handle_message("wss://s.com", "alert:critical", tenant_id="t1")
    mock_dispatcher.dispatch.assert_called_once()


# ── PriceThresholdPoller ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_price_above_threshold_fires():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.PRICE_THRESHOLD, price_symbol="BTC", price_threshold=60000.0, price_direction="above")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    poller = PriceThresholdPoller(trigger_store=store, dispatcher=mock_dispatcher)

    # First price check — starts below
    poller._last_prices["BTC"] = 55000.0
    await poller.check_price("BTC", 65000.0, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_price_below_threshold_fires():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.PRICE_THRESHOLD, price_symbol="ETH", price_threshold=2000.0, price_direction="below")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    poller = PriceThresholdPoller(trigger_store=store, dispatcher=mock_dispatcher)
    poller._last_prices["ETH"] = 2500.0
    await poller.check_price("ETH", 1900.0, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_price_already_above_does_not_double_fire():
    """Price already above threshold should not re-fire."""
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.PRICE_THRESHOLD, price_symbol="SOL", price_threshold=100.0, price_direction="above")
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    poller = PriceThresholdPoller(trigger_store=store, dispatcher=mock_dispatcher)
    poller._last_prices["SOL"] = 150.0  # already above
    await poller.check_price("SOL", 160.0, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_price_wrong_symbol_no_fire():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.PRICE_THRESHOLD, price_symbol="DOGE", price_threshold=0.1)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="test")
    mock_dispatcher = AsyncMock()
    poller = PriceThresholdPoller(trigger_store=store, dispatcher=mock_dispatcher)
    await poller.check_price("BTC", 70000.0, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
