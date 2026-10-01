"""PubSubConnector — Google Cloud Pub/Sub ingestion.

Streaming: pull subscription, acknowledges messages after indexing.
Cursor: subscription name (stateless — GCP manages offset).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth, stable_doc_id
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("pubsub", feature_flag="ingestion_connector_pubsub_enabled")
class PubSubConnector(BaseConnector):
    """Google Cloud Pub/Sub connector — pull subscriptions."""

    source_type = "pubsub"
    supports_streaming = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            from google.cloud import pubsub_v1  # type: ignore[import-not-found]

            cc = config.connection_config
            project = cc.get("project", "")
            sub_name = cc.get("subscription", "")
            full_sub = f"projects/{project}/subscriptions/{sub_name}"

            def _probe() -> None:
                subscriber = pubsub_v1.SubscriberClient()
                subscriber.get_subscription(subscription=full_sub)

            await run_blocking(_probe)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency, metadata={"project": project, "subscription": sub_name}
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="google-cloud-pubsub not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from google.cloud import pubsub_v1  # type: ignore[import-not-found]
        except ImportError:
            _log.error("google-cloud-pubsub not installed")
            return

        cc = config.connection_config
        project = cc.get("project", "")
        sub_name = cc.get("subscription", "")
        full_sub = f"projects/{project}/subscriptions/{sub_name}"
        batch_size = int(cc.get("batch_size", 100))

        def _pull():
            subscriber = pubsub_v1.SubscriberClient()
            resp = subscriber.pull(subscription=full_sub, max_messages=batch_size, timeout=10)
            ack_ids = []
            messages = []
            for m in resp.received_messages:
                ack_ids.append(m.ack_id)
                data = m.message.data
                attrs = dict(m.message.attributes)
                messages.append((data, attrs, m.message.message_id))
            if ack_ids:
                subscriber.acknowledge(subscription=full_sub, ack_ids=ack_ids)
            return messages

        messages = await run_blocking(_pull)

        new_cursor = cursor or sub_name
        for data, attrs, msg_id in messages:
            try:
                text = json.dumps(json.loads(data))
            except Exception:
                text = (
                    data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
                )

            doc = RawDocument(
                doc_id=stable_doc_id(config, sub_name, msg_id),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"pubsub://{project}/{sub_name}/{msg_id}",
                content=text.encode(),
                content_type="application/json" if text.startswith("{") else "text/plain",
                metadata={
                    "project": project,
                    "subscription": sub_name,
                    "message_id": msg_id,
                    **attrs,
                },
            )
            yield doc, new_cursor
