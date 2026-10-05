"""ConnectorRegistry — maps source_type strings to connector classes.

LAW-20: Every connector is gated by a feature flag.
All connectors self-register via the @register decorator.
No central list to maintain — just add @register and import.

Usage in a connector file:
    from app.ingestion.connector_registry import register

    @register("s3")
    class S3Connector(BaseConnector):
        source_type = "s3"
        ...

The registry is populated when connectors are imported. Import all
connectors in get_all_connectors() to ensure they are registered.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ingestion.base_connector import BaseConnector

_log = logging.getLogger(__name__)

# Global registry: source_type → connector class
_REGISTRY: dict[str, type[BaseConnector]] = {}

# Feature flags: source_type → settings attribute name
_FEATURE_FLAGS: dict[str, str] = {}

# Connector modules that failed to import: module path → error. Reported by
# get_connector so the Sources UI shows *why* a connector is missing instead
# of a bare "not registered".
_LOAD_ERRORS: dict[str, str] = {}


def register(source_type: str, *, feature_flag: str | None = None):
    """Class decorator: register a connector in the global registry.

    Args:
        source_type: The string key (e.g. "s3", "snowflake", "slack")
        feature_flag: Optional Settings attribute that gates this connector
                      (LAW-20: dark-launch pattern). If None, always enabled.

    Example:
        @register("s3", feature_flag="ingestion_connector_s3_enabled")
        class S3Connector(BaseConnector):
            source_type = "s3"
    """

    def decorator(cls: type[BaseConnector]) -> type[BaseConnector]:
        if source_type in _REGISTRY:
            _log.warning(
                "connector_registry_overwrite source_type=%s old=%s new=%s",
                source_type,
                _REGISTRY[source_type].__name__,
                cls.__name__,
            )
        _REGISTRY[source_type] = cls
        if feature_flag:
            _FEATURE_FLAGS[source_type] = feature_flag
        _log.debug("connector_registered source_type=%s class=%s", source_type, cls.__name__)
        return cls

    return decorator


def get_connector(source_type: str, *, settings: object | None = None) -> type[BaseConnector]:
    """Return the connector class for source_type.

    Args:
        source_type: e.g. "s3", "snowflake", "kafka"
        settings: App settings object. If provided, feature flag checked.

    Raises:
        KeyError: source_type not registered
        RuntimeError: feature flag disabled for this source_type
    """
    if source_type not in _REGISTRY:
        failed = connector_load_error(source_type)
        if failed:
            raise KeyError(
                f"The {source_type!r} connector is unavailable on this server: {failed}"
            )
        raise KeyError(
            f"No connector registered for source_type={source_type!r}. "
            f"Available: {sorted(_REGISTRY)}"
        )

    # LAW-20: Feature flag check. Every caller (scheduler, sources API, Celery
    # sync task) used to call this without ``settings``, so the flags were never
    # enforced at all; fall back to the process settings instead.
    if settings is None and source_type in _FEATURE_FLAGS:
        from app.core.config import get_settings

        settings = get_settings()
    if settings is not None and source_type in _FEATURE_FLAGS:
        flag_attr = _FEATURE_FLAGS[source_type]
        enabled = getattr(settings, flag_attr, True)
        if not enabled:
            raise ConnectorDisabledError(
                f"Connector for source_type={source_type!r} is disabled by "
                f"feature flag {flag_attr}=False. Set it to True to enable."
            )

    # A connector whose SDK is not installed cannot run: refuse it here with the
    # reason (the scheduler records it on a failed job) rather than let a sync
    # start and end empty.
    from app.ingestion.base_connector import ConnectorUnavailableError
    from app.ingestion.connector_sdks import unavailable_reason

    reason = unavailable_reason(source_type)
    if reason:
        raise ConnectorUnavailableError(
            f"The {source_type!r} connector is unavailable on this server: {reason}"
        )

    return _REGISTRY[source_type]


class ConnectorDisabledError(RuntimeError):
    """The operator turned this connector off with its feature flag (kill switch)."""


def connector_load_error(source_type: str) -> str:
    """Why the module that would provide ``source_type`` failed to import, or ``""``."""
    for module_path, error in _LOAD_ERRORS.items():
        stem = module_path.rsplit(".", 1)[-1].removesuffix("_connector")
        if stem == source_type or stem.startswith(f"{source_type}_"):
            return f"connector module {module_path} failed to load ({error})"
    return ""


def connector_error_message(exc: BaseException) -> str:
    """The human-readable message of a get_connector failure (KeyError reprs its arg)."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def list_registered() -> list[str]:
    """List all registered source types."""
    return sorted(_REGISTRY.keys())


def get_connector_metadata() -> list[dict]:
    """Return metadata for all registered connectors (for catalogue API)."""
    from app.ingestion.connector_sdks import CONNECTOR_SDKS, unavailable_reason

    result = []
    for source_type, cls in sorted(_REGISTRY.items()):
        instance = cls.__new__(cls)
        reason = unavailable_reason(source_type)
        result.append(
            {
                "source_type": source_type,
                "class": cls.__name__,
                "supports_streaming": getattr(instance, "supports_streaming", False),
                "supports_acl": getattr(instance, "supports_acl_propagation", False),
                "supports_deletion": getattr(instance, "supports_deletion_tracking", False),
                "feature_flag": _FEATURE_FLAGS.get(source_type),
                # The Sources UI greys out a connector whose SDK is missing.
                "available": not reason,
                "unavailable_reason": reason,
                "required_packages": sorted(
                    {req.package for req in CONNECTOR_SDKS.get(source_type, ())}
                ),
            }
        )
    return result


def load_all_connectors() -> None:
    """Import all connector modules to trigger @register decorators.

    Call this once at app startup to populate the registry.
    New connectors are discovered automatically by adding them here.
    """
    connector_modules = [
        # Tier 1 — Document stores (existing migrated)
        "app.ingestion.connectors.gdrive_connector",
        "app.ingestion.connectors.notion_connector",
        "app.ingestion.connectors.sharepoint_connector",
        "app.ingestion.connectors.slack_connector",
        "app.ingestion.connectors.github_connector",
        "app.ingestion.connectors.pdf_file_connector",
        "app.ingestion.connectors.agent_generated_connector",
        "app.ingestion.connectors.s3_connector",
        "app.ingestion.connectors.web_crawl_connector",
        "app.ingestion.connectors.postgresql_connector",
        # Tier 2 — Object storage + Analytics
        "app.ingestion.connectors.gcs_connector",
        "app.ingestion.connectors.azure_blob_connector",
        "app.ingestion.connectors.minio_connector",
        "app.ingestion.connectors.snowflake_connector",
        "app.ingestion.connectors.bigquery_connector",
        "app.ingestion.connectors.clickhouse_connector",
        "app.ingestion.connectors.duckdb_connector",
        # Tier 3 — Communication + Developer + Web
        "app.ingestion.connectors.teams_connector",
        "app.ingestion.connectors.email_imap_connector",
        "app.ingestion.connectors.confluence_connector",
        "app.ingestion.connectors.jira_connector",
        "app.ingestion.connectors.rss_connector",
        "app.ingestion.connectors.http_connector",
        "app.ingestion.connectors.youtube_connector",
        "app.ingestion.connectors.arxiv_connector",
        "app.ingestion.connectors.gitlab_connector",
        "app.ingestion.connectors.discord_connector",
        # Tier 4 — Streaming + Databases + CRM
        "app.ingestion.connectors.kafka_connector",
        "app.ingestion.connectors.kinesis_connector",
        "app.ingestion.connectors.pubsub_connector",
        "app.ingestion.connectors.mysql_connector",
        "app.ingestion.connectors.mongodb_connector",
        "app.ingestion.connectors.elasticsearch_connector",
        "app.ingestion.connectors.salesforce_connector",
        "app.ingestion.connectors.hubspot_connector",
        "app.ingestion.connectors.zendesk_connector",
        "app.ingestion.connectors.servicenow_connector",
        # Tier 5 — IoT + Scientific + Specialized
        "app.ingestion.connectors.mqtt_connector",
        "app.ingestion.connectors.influxdb_connector",
        "app.ingestion.connectors.pagerduty_connector",
        "app.ingestion.connectors.sentry_connector",
        "app.ingestion.connectors.neo4j_connector",
        "app.ingestion.connectors.redis_connector",
    ]
    import importlib

    for module_path in connector_modules:
        try:
            importlib.import_module(module_path)
        except Exception as exc:
            # Not DEBUG: a connector the catalogue offers but cannot load is an
            # operator problem, and the Sources API reports it per source type.
            _LOAD_ERRORS[module_path] = f"{type(exc).__name__}: {exc}"
            _log.warning("connector_module_error %s: %s", module_path, exc)
        else:
            _LOAD_ERRORS.pop(module_path, None)
