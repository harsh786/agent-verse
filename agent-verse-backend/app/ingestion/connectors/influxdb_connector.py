"""InfluxDBConnector — InfluxDB time-series database ingestion.

Cursor: last measurement timestamp (RFC3339 / Unix nanosecond epoch).
Supports InfluxDB 2.x (Flux queries) and 3.x.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
    stable_doc_id,
)
from app.ingestion.connector_egress import pin_source_urls, run_driver_call
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


_NON_IDENTITY_COLUMNS = frozenset({"_value", "result", "table", "_start", "_stop"})


def _point_identity(row: dict[str, Any]) -> list[str]:
    """A point is identified by measurement, field, tags and time — not its value."""
    return [f"{k}={v}" for k, v in sorted(row.items()) if k not in _NON_IDENTITY_COLUMNS]


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

            def _health() -> tuple[bool, str]:
                from influxdb_client import InfluxDBClient  # type: ignore[import-not-found]

                with InfluxDBClient(
                    url=url,
                    token=cc.get("token", ""),
                    org=cc.get("org", ""),
                ) as client:
                    # health() is deprecated in influxdb-client; ping() is its
                    # replacement but answers only True/False, so on failure the
                    # reason is read from the same /ping endpoint via version().
                    if client.ping():
                        return True, str(client.version())
                    try:
                        client.version()
                    except Exception as exc:
                        return False, f"Unhealthy: {exc}"
                    return False, "Unhealthy: InfluxDB did not answer /ping"

            # The HTTP client's own lookups answer with the checked addresses, and
            # a redirect it follows (urllib3 does by default) is egress-checked.
            async with pin_source_urls([url], context="influxdb"):
                ok, detail = await run_driver_call(_health, context="influxdb")
            latency = (time.perf_counter() - t0) * 1000
            if ok:
                return ConnectionHealth(ok=True, latency_ms=latency, metadata={"version": detail})
            return ConnectionHealth(ok=False, error=detail)
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
            except ImportError as exc:
                # Returning nothing here reported a successful, empty sync.
                raise ConnectorUnavailableError(
                    "influxdb-client is not installed on this server; the connector cannot run"
                ) from exc
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
                doc_id=stable_doc_id(config, *_point_identity(row)),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"{url}/orgs/{org}/buckets/{bucket}/measurements/{measurement}",
                content=text.encode(),
                content_type="text/plain",
                metadata={"measurement": row.get("_measurement"), "ts": ts, "bucket": bucket},
            )
            yield doc, new_cursor
