"""S3Connector — AWS S3 / S3-compatible (MinIO, R2) ingestion.

Incremental: ListObjectsV2 with StartAfter cursor (LastModified-based).
Streaming: S3 event notifications via SQS or EventBridge.
Supports any file format via ParserRegistry dispatch.
"""

from __future__ import annotations

import contextlib
import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
)
from app.ingestion.connector_egress import (
    pin_source_urls,
    pin_source_urls_sync,
    run_driver_call,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import iterate_blocking, run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("s3", feature_flag="ingestion_connector_s3_enabled")
class S3Connector(BaseConnector):
    """AWS S3 and S3-compatible object storage ingestion."""

    source_type = "s3"
    supports_streaming = True
    supports_deletion_tracking = True
    # S3-compatible stores (MinIO) have no public default endpoint.
    _requires_endpoint = False

    def _configured_endpoint(self, config: SourceConfig) -> str | None:
        endpoint = config.connection_config.get("endpoint_url") or None
        if endpoint is None and not self._requires_endpoint:
            return None
        return str(endpoint or "")

    @contextlib.asynccontextmanager
    async def _pinned_endpoint(self, config: SourceConfig) -> AsyncIterator[str | None]:
        """The tenant's ``endpoint_url``, egress-checked and pinned for the block.

        boto3 sends every request (with the tenant's signed credentials) to this
        URL, and the objects it returns are indexed into the tenant's collection —
        so an endpoint of ``http://169.254.169.254`` / the platform's own MinIO was
        an SSRF straight into the knowledge base. Inside the block boto3's own
        lookups of the endpoint host answer with the checked addresses only (no
        DNS-rebinding window). ``None`` (no endpoint) means AWS's own regional
        endpoint, which needs no check.
        """
        endpoint = self._configured_endpoint(config)
        if endpoint is None:
            yield None
            return
        async with pin_source_urls([endpoint], context=self.source_type):
            yield endpoint

    @contextlib.contextmanager
    def _pinned_endpoint_sync(self, config: SourceConfig) -> Iterator[str | None]:
        endpoint = self._configured_endpoint(config)
        if endpoint is None:
            yield None
            return
        with pin_source_urls_sync([endpoint], context=self.source_type):
            yield endpoint

    def _runner(self, endpoint_url: str | None) -> Callable[..., Awaitable[Any]]:
        """How to run a blocking boto3 call off the event loop.

        A tenant ``endpoint_url`` gets the egress-checked driver runner (every
        host boto3 resolves is checked); AWS's own endpoints get the plain SDK
        pool — they may legitimately resolve to private VPC-endpoint addresses.
        """
        if endpoint_url is None:
            return run_blocking
        return functools.partial(run_driver_call, context=self.source_type)

    @staticmethod
    def _client_kwargs(endpoint_url: str | None) -> dict[str, Any]:
        """boto3 client kwargs. A custom endpoint is addressed path-style, so every
        request goes to the checked host — never ``<bucket>.<host>``, a name the
        egress check never saw."""
        if endpoint_url is None:
            return {}
        from botocore.config import Config  # type: ignore[import-not-found]

        return {"endpoint_url": endpoint_url, "config": Config(s3={"addressing_style": "path"})}

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            bucket = config.connection_config.get("bucket", "")
            region = config.connection_config.get("region", "us-east-1")
            credentials = config.connection_config.get("credentials", {})

            async with self._pinned_endpoint(config) as endpoint_url:
                import boto3  # type: ignore[import-not-found]

                def _probe() -> tuple[float, Any]:
                    session = boto3.Session(
                        aws_access_key_id=credentials.get("access_key_id"),
                        aws_secret_access_key=credentials.get("secret_access_key"),
                        region_name=region,
                    )
                    s3 = session.client("s3", **self._client_kwargs(endpoint_url))
                    # Quick check: head bucket
                    s3.head_bucket(Bucket=bucket)
                    latency = (time.perf_counter() - t0) * 1000
                    # Estimate doc count
                    resp = s3.list_objects_v2(
                        Bucket=bucket,
                        Prefix=config.connection_config.get("prefix", ""),
                        MaxKeys=1,
                    )
                    return latency, resp

                latency, resp = await self._runner(endpoint_url)(_probe)
            key_count = resp.get("KeyCount", 0)
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"bucket": bucket, "region": region, "accessible_objects": key_count},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="boto3 not installed — pip install boto3")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """List S3 objects sorted by LastModified, yield those newer than cursor."""

        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "boto3 is not installed on this server; the connector cannot run"
            ) from exc

        bucket = config.connection_config.get("bucket", "")
        prefix = config.connection_config.get("prefix", "")
        region = config.connection_config.get("region", "us-east-1")
        credentials = config.connection_config.get("credentials", {})
        include_patterns = config.include_patterns
        exclude_patterns = config.exclude_patterns

        try:
            async with self._pinned_endpoint(config) as endpoint_url:
                async for raw, new_cursor in self._iter_objects(
                    boto3,
                    endpoint_url,
                    config,
                    bucket=bucket,
                    prefix=prefix,
                    region=region,
                    credentials=credentials,
                    cursor=cursor,
                    include_patterns=include_patterns,
                    exclude_patterns=exclude_patterns,
                ):
                    yield raw, new_cursor
        except Exception as exc:
            _log.error("s3_connector_error bucket=%s: %s", bucket, exc)
            raise

    async def _iter_objects(
        self,
        boto3: Any,
        endpoint_url: str | None,
        config: SourceConfig,
        *,
        bucket: str,
        prefix: str,
        region: str,
        credentials: dict[str, Any],
        cursor: str | None,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        run = self._runner(endpoint_url)

        def _client() -> Any:
            session = boto3.Session(
                aws_access_key_id=credentials.get("access_key_id"),
                aws_secret_access_key=credentials.get("secret_access_key"),
                region_name=region,
            )
            return session.client("s3", **self._client_kwargs(endpoint_url))

        def _download(key: str) -> tuple[bytes, str]:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read()
            return body, response.get("ContentType", "application/octet-stream")

        # boto3 is blocking: client setup, each page and each download run on
        # the SDK pool (see app.ingestion.sdk_executor), never on the loop.
        s3 = await run(_client)
        pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)

        new_cursor = cursor or ""
        async for page in iterate_blocking(pages, chunk_size=1, runner=run):
            objects = sorted(
                page.get("Contents", []),
                key=lambda o: o["LastModified"].isoformat(),
            )
            for obj in objects:
                key = obj["Key"]
                last_modified = obj["LastModified"].isoformat()

                # Skip objects older than cursor (already ingested)
                if cursor and last_modified <= cursor:
                    continue

                # Apply include/exclude patterns
                if not self._matches_patterns(key, include_patterns, exclude_patterns):
                    continue

                # Check file size
                if obj.get("Size", 0) > config.max_doc_size_bytes:
                    _log.debug("s3_skip_too_large key=%s size=%d", key, obj["Size"])
                    continue

                # Download object
                try:
                    content_bytes, content_type = await run(_download, key)
                except Exception as e:
                    _log.warning("s3_download_error key=%s: %s", key, e)
                    continue

                raw = RawDocument(
                    doc_id=f"s3://{bucket}/{key}",
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    content=content_bytes,
                    content_type=content_type,
                    source_url=f"s3://{bucket}/{key}",
                    title=key.split("/")[-1],
                    modified_at=last_modified,
                    metadata={"s3_key": key, "s3_bucket": bucket, "size": obj["Size"]},
                )
                if last_modified > new_cursor:
                    new_cursor = last_modified

                yield raw, new_cursor

    async def list_live_doc_ids(self, config: SourceConfig) -> set[str] | None:
        """Every object key under the configured prefix/patterns (upstream deletions)."""
        import boto3

        cc = config.connection_config
        bucket = cc.get("bucket", "")
        prefix = cc.get("prefix", "")
        credentials = cc.get("credentials", {})
        async with self._pinned_endpoint(config) as endpoint_url:

            def _list() -> list[str]:
                session = boto3.Session(
                    aws_access_key_id=credentials.get("access_key_id"),
                    aws_secret_access_key=credentials.get("secret_access_key"),
                    region_name=cc.get("region", "us-east-1"),
                )
                client = session.client("s3", **self._client_kwargs(endpoint_url))
                keys: list[str] = []
                paginator = client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    keys.extend(obj["Key"] for obj in page.get("Contents", []))
                return keys

            keys = await self._runner(endpoint_url)(_list)
        return {
            f"s3://{bucket}/{key}"
            for key in keys
            if self._matches_patterns(key, config.include_patterns, config.exclude_patterns)
        }

    def manages_doc_id(self, doc_id: str) -> bool:
        return str(doc_id).startswith("s3://")

    async def on_webhook(
        self,
        config: SourceConfig,
        payload: bytes,
        headers: dict[str, str],
    ) -> AsyncIterator[RawDocument]:
        """Handle S3 event notifications (SQS or EventBridge)."""
        import json

        try:
            data = json.loads(payload)
        except Exception:
            return

        # Handle SQS-wrapped S3 events
        records = data.get("Records", [])
        for record in records:
            event_name = record.get("eventName", "")
            if "ObjectCreated" in event_name or "ObjectModified" in event_name:
                s3_info = record.get("s3", {})
                bucket = s3_info.get("bucket", {}).get("name", "")
                key = s3_info.get("object", {}).get("key", "")
                if bucket and key:
                    # Reuse get_delta for single-object fetch
                    async for raw, _cursor in self._fetch_single(config, bucket, key):
                        yield raw

    async def _fetch_single(
        self, config: SourceConfig, bucket: str, key: str
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Fetch and yield a single S3 object."""
        from app.ingestion.source_config import RawDocument

        try:
            import boto3

            credentials = config.connection_config.get("credentials", {})
            async with self._pinned_endpoint(config) as endpoint_url:

                def _fetch() -> tuple[bytes, str]:
                    s3 = boto3.client(
                        "s3",
                        aws_access_key_id=credentials.get("access_key_id"),
                        aws_secret_access_key=credentials.get("secret_access_key"),
                        region_name=config.connection_config.get("region", "us-east-1"),
                        **self._client_kwargs(endpoint_url),
                    )
                    response = s3.get_object(Bucket=bucket, Key=key)
                    body = response["Body"].read()
                    return body, response.get("ContentType", "application/octet-stream")

                content_bytes, content_type = await self._runner(endpoint_url)(_fetch)
            raw = RawDocument(
                doc_id=f"s3://{bucket}/{key}",
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                content=content_bytes,
                content_type=content_type,
                source_url=f"s3://{bucket}/{key}",
                title=key.split("/")[-1],
            )
            yield raw, key
        except Exception as exc:
            _log.warning("s3_fetch_single_error bucket=%s key=%s: %s", bucket, key, exc)

    def estimate_doc_count(self, config: SourceConfig) -> int | None:
        try:
            import boto3

            credentials = config.connection_config.get("credentials", {})
            with self._pinned_endpoint_sync(config) as endpoint_url:
                s3 = boto3.client(
                    "s3",
                    aws_access_key_id=credentials.get("access_key_id"),
                    aws_secret_access_key=credentials.get("secret_access_key"),
                    region_name=config.connection_config.get("region", "us-east-1"),
                    **self._client_kwargs(endpoint_url),
                )
                resp = s3.list_objects_v2(
                    Bucket=config.connection_config.get("bucket", ""),
                    Prefix=config.connection_config.get("prefix", ""),
                    MaxKeys=1,
                )
            # S3 KeyCount is limited to page size; return as lower bound
            return resp.get("KeyCount")
        except Exception:
            return None

    @staticmethod
    def _matches_patterns(
        key: str,
        include: list[str],
        exclude: list[str],
    ) -> bool:
        """Check include/exclude glob/extension patterns."""
        import fnmatch

        if exclude:
            for pat in exclude:
                if fnmatch.fnmatch(key, pat):
                    return False
        if include:
            return any(fnmatch.fnmatch(key, pat) for pat in include)
        return True
