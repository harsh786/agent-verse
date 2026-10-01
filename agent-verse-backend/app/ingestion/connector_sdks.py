"""Which third-party SDK each ingestion connector needs at runtime.

Connectors import their SDK lazily, so a missing package used to surface only
deep inside a sync — often as an empty "successful" one. This table is the single
place that says what each connector needs; the registry uses it to refuse an
unavailable connector with a clear reason and the Sources catalogue to mark it
unavailable. The packages ship in the image through the ``connectors`` extra
(pyproject.toml); ``tests/ingestion/test_connector_sdks.py`` keeps this table,
the extra and the registry in sync.

Connectors that only need core dependencies (httpx, stdlib) are absent here.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

__all__ = ["CONNECTOR_SDKS", "SdkRequirement", "missing_sdks", "unavailable_reason"]


@dataclass(frozen=True)
class SdkRequirement:
    module: str  # importable module, e.g. "google.cloud.bigquery"
    package: str  # distribution that provides it, e.g. "google-cloud-bigquery"


def _req(module: str, package: str | None = None) -> SdkRequirement:
    return SdkRequirement(module=module, package=package or module)


_BOTO3 = (_req("boto3"),)

CONNECTOR_SDKS: dict[str, tuple[SdkRequirement, ...]] = {
    "s3": _BOTO3,
    "minio": _BOTO3,
    "kinesis": _BOTO3,
    "gcs": (_req("google.cloud.storage", "google-cloud-storage"),),
    "bigquery": (_req("google.cloud.bigquery", "google-cloud-bigquery"),),
    "pubsub": (_req("google.cloud.pubsub_v1", "google-cloud-pubsub"),),
    "gdrive": (
        _req("googleapiclient", "google-api-python-client"),
        _req("google.oauth2", "google-auth"),
    ),
    "azure_blob": (_req("azure.storage.blob", "azure-storage-blob"),),
    "snowflake": (_req("snowflake.connector", "snowflake-connector-python"),),
    "clickhouse": (_req("clickhouse_connect", "clickhouse-connect"),),
    "duckdb": (_req("duckdb"),),
    "kafka": (_req("confluent_kafka", "confluent-kafka"),),
    "mysql": (_req("pymysql", "PyMySQL"),),
    "mariadb": (_req("pymysql", "PyMySQL"),),
    "mongodb": (_req("pymongo"),),
    "postgresql": (_req("asyncpg"),),
    "redis": (_req("redis"),),
    "mqtt": (_req("paho.mqtt.client", "paho-mqtt"),),
    "influxdb": (_req("influxdb_client", "influxdb-client"),),
    "neo4j": (_req("neo4j"),),
    "rss": (_req("feedparser"),),
    "atom": (_req("feedparser"),),
    "youtube": (_req("youtube_transcript_api", "youtube-transcript-api"),),
}


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # A parent package is missing (or was blocked by sys.modules[...] = None).
        return False


def missing_sdks(source_type: str) -> list[SdkRequirement]:
    """The SDK requirements of ``source_type`` that are not importable here."""
    return [req for req in CONNECTOR_SDKS.get(source_type, ()) if not _importable(req.module)]


def unavailable_reason(source_type: str) -> str:
    """Why ``source_type`` cannot run on this server (``""`` when it can)."""
    missing = missing_sdks(source_type)
    if not missing:
        return ""
    packages = ", ".join(sorted({req.package for req in missing}))
    return (
        f"the {source_type!r} connector needs {packages}, which is not installed on this "
        "server (install the backend with the 'connectors' extra)"
    )
