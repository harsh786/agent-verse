"""InfluxDBConnector — InfluxDB time-series database ingestion.

Cursor: last measurement timestamp (RFC3339 / Unix nanosecond epoch).
Supports InfluxDB 2.x (Flux queries) and 3.x.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_egress import pin_source_urls, run_driver_call
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("influxdb", feature_flag="ingestion_connector_influxdb_enabled")
class InfluxDBConnector(BaseConnector):
    """InfluxDB 2.x time-series connector — Flux query based."""

    source_type = "influxdb"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            # Tenant-supplied URL: egress-guarded (it used to default to the
            # platform's own http://localhost:8086).
            url = cc.get("url", "")

            def _health() -> Any:
                from influxdb_client import InfluxDBClient  # type: ignore[import-not-found]

                with InfluxDBClient(
                    url=url,
                    token=cc.get("token", ""),
                    org=cc.get("org", ""),
                ) as client:
                    return client.health()

            # The HTTP client's own lookups answer with the checked addresses, and
            # a redirect it follows (urllib3 does by default) is egress-checked.
            async with pin_source_urls([url], context="influxdb"):
                health = await run_driver_call(_health, context="influxdb")
            latency = (time.perf_counter() - t0) * 1000
            if health.status == "pass":
                return ConnectionHealth(
                    ok=True, latency_ms=latency, metadata={"version": health.version}
                )
            return ConnectionHealth(ok=False, error=f"Unhealthy: {health.message}")
        except ImportError:
            return ConnectionHealth(
                ok=False, error="influxdb-client not installed — pip install influxdb-client"
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        url = cc.get("url", "")
        token = cc.get("token", "")
        org = cc.get("org", "")
        bucket = cc.get("bucket", "")
        measurement = cc.get("measurement", "")
        range_start = cursor or "-30d"
        batch_size = int(cc.get("batch_size", 500))

        flux_query = f'from(bucket: "{bucket}")\n  |> range(start: {range_start})\n'
        if measurement:
            flux_query += f'  |> filter(fn: (r) => r._measurement == "{measurement}")\n'
        flux_query += f"  |> limit(n: {batch_size})\n"

        if cc.get("flux_query"):
            flux_query = cc["flux_query"].replace("{range_start}", range_start)

        def _query():
            from influxdb_client import InfluxDBClient

            with InfluxDBClient(url=url, token=token, org=org) as client:
                tables = client.query_api().query(flux_query, org=org)
                rows = []
                for table in tables:
                    for record in table.records:
                        rows.append(record.values)
                return rows

        async with pin_source_urls([url], context="influxdb"):
            try:
                import influxdb_client  # type: ignore[import-not-found]  # noqa: F401
            except ImportError:
                _log.error("influxdb-client not installed")
                return
            rows = await run_driver_call(_query, context="influxdb")

        new_cursor = cursor or range_start
        for row in rows:
            ts = str(row.get("_time", ""))
            new_cursor = max(new_cursor, ts) if ts else new_cursor
            text_parts = [
                f"{k}: {v}" for k, v in row.items() if v is not None and not k.startswith("_")
            ]
            text = f"Measurement: {row.get('_measurement', '')}\nTimestamp: {ts}\n" + "\n".join(
                text_parts
            )
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"{url}/orgs/{org}/buckets/{bucket}/measurements/{measurement}",
                content=text.encode(),
                content_type="text/plain",
                metadata={"measurement": row.get("_measurement"), "ts": ts, "bucket": bucket},
            )
            yield doc, new_cursor
