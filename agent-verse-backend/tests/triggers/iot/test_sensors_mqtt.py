"""Tests for IoT modules — MQTT consumer and sensor threshold evaluator."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.triggers.iot.geofence import (
    GeofenceRegion,
    GeofenceTriggerEvaluator,
    LatLng,
    haversine_meters,
)
from app.triggers.iot.mqtt import MQTTTriggerConsumer
from app.triggers.iot.sensor import SensorThresholdEvaluator, convert_to_base
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

# ── MQTT ──────────────────────────────────────────────────────────────────────

def test_mqtt_exact_match():
    c = MQTTTriggerConsumer()
    assert c._topic_matches("a/b/c", "a/b/c") is True


def test_mqtt_plus_wildcard_single():
    c = MQTTTriggerConsumer()
    assert c._topic_matches("sensors/+/temp", "sensors/dev1/temp") is True
    assert c._topic_matches("sensors/+/temp", "sensors/dev1/humidity") is False


def test_mqtt_hash_wildcard_multilevel():
    c = MQTTTriggerConsumer()
    assert c._topic_matches("sensors/#", "sensors/room1/temp/celsius") is True
    assert c._topic_matches("sensors/#", "actuators/fan") is False


def test_mqtt_no_match():
    c = MQTTTriggerConsumer()
    assert c._topic_matches("a/b", "a/c") is False


@pytest.mark.asyncio
async def test_mqtt_no_client_start():
    c = MQTTTriggerConsumer()
    await c.start()  # should not raise
    await c.stop()


@pytest.mark.asyncio
async def test_mqtt_handle_fires_matching():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(trigger_type=TriggerType.MQTT)
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="MQTT message")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    consumer = MQTTTriggerConsumer(trigger_store=store, dispatcher=mock_dispatcher)
    await consumer.handle_message("sensors/temp", json.dumps({"t": 25}).encode(), tenant_id="t1")
    assert mock_dispatcher.dispatch.called


# ── Sensor threshold ──────────────────────────────────────────────────────────

def test_sensor_check_above():
    ev = SensorThresholdEvaluator()
    assert ev.check_threshold(85.0, 80.0, ">") is True


def test_sensor_check_below():
    ev = SensorThresholdEvaluator()
    assert ev.check_threshold(70.0, 80.0, "<") is True


def test_sensor_check_equal():
    ev = SensorThresholdEvaluator()
    assert ev.check_threshold(42.0, 42.0, "==") is True


def test_sensor_unit_conversion_fahrenheit():
    # 212°F = 100°C
    assert abs(convert_to_base(212, "fahrenheit") - 100.0) < 0.01


def test_sensor_unit_conversion_kelvin():
    # 373.15K = 100°C
    assert abs(convert_to_base(373.15, "kelvin") - 100.0) < 0.01


@pytest.mark.asyncio
async def test_sensor_dispatch_on_crossing():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.SENSOR_THRESHOLD,
        sensor_metric="temperature",
        sensor_threshold=80.0,
        sensor_comparison=">",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="Temp alert")
    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace())
    evaluator = SensorThresholdEvaluator(trigger_store=store, dispatcher=mock_dispatcher)
    await evaluator.evaluate("sensor-001", "temperature", 90.0, tenant_id="t1")
    assert mock_dispatcher.dispatch.called


@pytest.mark.asyncio
async def test_sensor_no_dispatch_below_threshold():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.SENSOR_THRESHOLD,
        sensor_metric="temperature",
        sensor_threshold=80.0,
        sensor_comparison=">",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="Temp alert")
    mock_dispatcher = AsyncMock()
    evaluator = SensorThresholdEvaluator(trigger_store=store, dispatcher=mock_dispatcher)
    await evaluator.evaluate("sensor-001", "temperature", 70.0, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_sensor_filters_by_metric():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.SENSOR_THRESHOLD,
        sensor_metric="humidity",
        sensor_threshold=90.0,
        sensor_comparison=">=",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template="Humidity alert")
    mock_dispatcher = AsyncMock()
    evaluator = SensorThresholdEvaluator(trigger_store=store, dispatcher=mock_dispatcher)
    # Different metric — should NOT fire
    await evaluator.evaluate("sensor-001", "temperature", 95.0, tenant_id="t1")
    mock_dispatcher.dispatch.assert_not_called()
