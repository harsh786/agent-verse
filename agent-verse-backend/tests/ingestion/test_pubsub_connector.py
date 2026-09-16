"""Tests for PubSubConnector — Google Cloud Pub/Sub pull-subscription ingestion.

google-cloud-pubsub is not installed in the test environment, so the
`google.cloud.pubsub_v1` module tree is faked via sys.modules injection.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors.pubsub_connector import PubSubConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-pubsub",
        tenant_id="t1",
        name="pubsub-src",
        family=SourceFamily.STREAMING,
        source_type="pubsub",
        connection_config=cc,
    )


def _fake_pubsub_modules(subscriber_client: MagicMock) -> dict:
    fake_pubsub_v1 = MagicMock()
    fake_pubsub_v1.SubscriberClient = MagicMock(return_value=subscriber_client)
    fake_google_cloud = MagicMock()
    # `from google.cloud import pubsub_v1` resolves via getattr first — a bare
    # MagicMock auto-vivifies an unrelated child on attribute access, so the
    # submodule must be wired explicitly to match the sys.modules entry.
    fake_google_cloud.pubsub_v1 = fake_pubsub_v1
    fake_google = MagicMock()
    fake_google.cloud = fake_google_cloud
    return {
        "google": fake_google,
        "google.cloud": fake_google_cloud,
        "google.cloud.pubsub_v1": fake_pubsub_v1,
    }


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    subscriber = MagicMock()
    subscriber.get_subscription = MagicMock()
    modules = _fake_pubsub_modules(subscriber)

    cfg = _config(project="proj", subscription="sub")
    with patch.dict("sys.modules", modules):
        health = await PubSubConnector().validate_connection(cfg)

    assert health.ok is True
    assert health.metadata == {"project": "proj", "subscription": "sub"}
    subscriber.get_subscription.assert_called_once_with(
        subscription="projects/proj/subscriptions/sub"
    )


@pytest.mark.asyncio
async def test_validate_connection_not_installed() -> None:
    with patch.dict("sys.modules", {"google.cloud.pubsub_v1": None}):
        health = await PubSubConnector().validate_connection(_config())
    assert health.ok is False
    assert "not installed" in health.error


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    subscriber = MagicMock()
    subscriber.get_subscription = MagicMock(side_effect=RuntimeError("no perms"))
    modules = _fake_pubsub_modules(subscriber)

    with patch.dict("sys.modules", modules):
        health = await PubSubConnector().validate_connection(_config(project="p", subscription="s"))

    assert health.ok is False
    assert "no perms" in health.error


@pytest.mark.asyncio
async def test_get_delta_not_installed_yields_nothing() -> None:
    with patch.dict("sys.modules", {"google.cloud.pubsub_v1": None}):
        docs = [d async for d in PubSubConnector().get_delta(_config(), None)]
    assert docs == []


def _make_message(ack_id: str, data: bytes, attrs: dict, msg_id: str) -> MagicMock:
    m = MagicMock()
    m.ack_id = ack_id
    m.message.data = data
    m.message.attributes = attrs
    m.message.message_id = msg_id
    return m


@pytest.mark.asyncio
async def test_get_delta_pulls_acks_and_transforms_json_messages() -> None:
    subscriber = MagicMock()
    resp = MagicMock()
    resp.received_messages = [
        _make_message("ack-1", b'{"foo": "bar"}', {"k": "v"}, "msg-1"),
    ]
    subscriber.pull = MagicMock(return_value=resp)
    subscriber.acknowledge = MagicMock()
    modules = _fake_pubsub_modules(subscriber)

    cfg = _config(project="proj", subscription="sub", batch_size=10)
    with patch.dict("sys.modules", modules):
        docs = [d async for d in PubSubConnector().get_delta(cfg, None)]

    assert len(docs) == 1
    doc, cursor = docs[0]
    assert doc.content_type == "application/json"
    assert b"foo" in doc.content
    assert doc.metadata["message_id"] == "msg-1"
    assert doc.metadata["k"] == "v"
    assert cursor == "sub"
    subscriber.acknowledge.assert_called_once_with(
        subscription="projects/proj/subscriptions/sub", ack_ids=["ack-1"]
    )


@pytest.mark.asyncio
async def test_get_delta_non_json_message_falls_back_to_text() -> None:
    subscriber = MagicMock()
    resp = MagicMock()
    resp.received_messages = [
        _make_message("ack-2", b"plain text body", {}, "msg-2"),
    ]
    subscriber.pull = MagicMock(return_value=resp)
    subscriber.acknowledge = MagicMock()
    modules = _fake_pubsub_modules(subscriber)

    cfg = _config(project="proj", subscription="sub")
    with patch.dict("sys.modules", modules):
        docs = [d async for d in PubSubConnector().get_delta(cfg, "existing-cursor")]

    assert len(docs) == 1
    doc, cursor = docs[0]
    assert doc.content_type == "text/plain"
    assert doc.content == b"plain text body"
    assert cursor == "existing-cursor"


@pytest.mark.asyncio
async def test_get_delta_no_messages_skips_ack() -> None:
    subscriber = MagicMock()
    resp = MagicMock()
    resp.received_messages = []
    subscriber.pull = MagicMock(return_value=resp)
    subscriber.acknowledge = MagicMock()
    modules = _fake_pubsub_modules(subscriber)

    cfg = _config(project="proj", subscription="sub")
    with patch.dict("sys.modules", modules):
        docs = [d async for d in PubSubConnector().get_delta(cfg, None)]

    assert docs == []
    subscriber.acknowledge.assert_not_called()
