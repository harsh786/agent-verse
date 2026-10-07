"""S3Connector — AWS S3 / S3-compatible (MinIO, R2) ingestion.

Incremental: ListObjectsV2 with StartAfter cursor (LastModified-based).
Streaming: S3 event notifications via SQS or EventBridge.
Supports any file format via ParserRegistry dispatch.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import functools
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorSecretsUndecryptableError,
    ConnectorUnavailableError,
    fetch_failure_document,
    refuse_undecryptable_secrets,
    stable_doc_id,
)
from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    pin_source_urls,
    pin_source_urls_sync,
    run_driver_call,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import iterate_blocking, run_blocking
from app.ingestion.source_config import CONNECTOR_LEGACY_DOC_ID_KEY
from app.net.aws_clients import (
    AwsCredentialError,
    AwsKeys,
    keys_from_config,
    resolve_region,
    tenant_client,
)

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


def _describe(exc: BaseException) -> str:
    """An honest one-line reason (S3 error code + message, or the exception)."""
    if isinstance(exc, ConnectorSecretsUndecryptableError | AwsCredentialError):
        return str(exc)
    try:
        from botocore import exceptions as boto_exc  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        boto_exc = None
    if boto_exc is not None and isinstance(exc, boto_exc.ClientError):
        error = exc.response.get("Error", {}) or {}
        status = (exc.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode", "")
        code = error.get("Code") or status
        return f"{code} {error.get('Message', '')} (HTTP {status})".strip()
    return f"{type(exc).__name__}: {exc}"[:500]


# CONNECTOR_REPLAY_KEY "kind" of a failed S3 object fetch (see replay_event).
_REPLAY_KIND = "s3_object"


def s3_document_id(config: SourceConfig, bucket: str, key: str) -> str:
    """The document id of an object: scoped to the Source (and the bucket).

    It used to be the bare ``s3://bucket/key``, so two Sources reading the same
    object into one collection (or two endpoints with the same bucket name)
    shared one document: the second was dedup-skipped or replaced the first,
    and it stayed attributed to whichever Source wrote it.
    """
    return stable_doc_id(config, f"s3://{bucket}/{key}")


def s3_legacy_document_id(bucket: str, key: str) -> str:
    """The pre-namespacing id (``s3://bucket/key``) of an object.

    Compat: documents indexed before stay searchable under it. A sync that
    re-fetches the object indexes it under :func:`s3_document_id` and the
    pipeline deletes the legacy copy in the same transaction — only when that
    copy is attributed to the same Source (``CONNECTOR_LEGACY_DOC_ID_KEY``).
    Reconciliation keeps a live object's legacy copy and deletes a removed
    object's (both ids are listed as live).
    """
    return f"s3://{bucket}/{key}"


class _OversizedObjectError(Exception):
    """An event's object is larger than the source's document size cap."""


# S3 error codes a later retry can succeed on (throttling, server-side trouble).
_RETRYABLE_S3_CODES = frozenset(
    {
        "SlowDown",
        "Throttling",
        "ThrottlingException",
        "RequestLimitExceeded",
        "RequestTimeout",
        "RequestTimeTooSkewed",
        "InternalError",
        "ServiceUnavailable",
    }
)


def _classify_fetch_error(exc: BaseException) -> tuple[str, bool]:
    """``(reason, retryable)`` for a failed S3 object fetch.

    Not retryable: the object or bucket is gone, access is denied, the request is
    malformed (other 4xx), credentials are missing, the egress policy refuses the
    endpoint, or the object is over the size cap. Retryable: throttling, 5xx,
    connection and timeout errors — and anything unrecognised, which is safer to
    retry (bounded by the DLQ retry cap) than to drop.
    """
    from app.ingestion.connector_egress import ConnectorEgressBlockedError

    if isinstance(exc, _OversizedObjectError):
        return str(exc), False
    if isinstance(exc, ConnectorEgressBlockedError):
        return f"egress refused: {exc}", False
    if isinstance(exc, AwsCredentialError):
        return f"s3 credentials invalid: {exc}", False
    try:
        from botocore import exceptions as boto_exc  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - boto3 is a core dependency
        boto_exc = None
    if boto_exc is not None:
        if isinstance(exc, boto_exc.ClientError):
            error = exc.response.get("Error", {}) or {}
            code = str(error.get("Code", "") or "")
            status = int((exc.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode", 0))
            retryable = code in _RETRYABLE_S3_CODES or status >= 500 or status in (408, 429)
            return f"s3 GetObject failed: {code or status} {error.get('Message', '')}".strip(), (
                retryable
            )
        if isinstance(exc, boto_exc.NoCredentialsError | boto_exc.PartialCredentialsError):
            return f"s3 credentials missing: {exc}", False
        if isinstance(exc, boto_exc.ParamValidationError):
            return f"invalid S3 request: {exc}", False
    return f"s3 fetch failed: {type(exc).__name__}: {exc}", True


# Objects modified up to this long before a run's listing started are listed
# again by the next run (re-fetched, then skipped by content-hash dedup when
# unchanged): it absorbs clock differences between the store's nodes and the
# second-precision Date header the watermark comes from.
_DEFAULT_LOOKBACK_SECONDS = 60
_ADDRESSING_STYLES = frozenset({"path", "virtual", "auto"})


@dataclasses.dataclass(frozen=True)
class _ListingCursor:
    """Incremental position of an S3 source (P1b-3).

    ``since``: objects with ``LastModified >= since`` are this run's delta.
    ``after``: the last key a run handed to the pipeline (keys are listed in
    lexicographic order); a resumed run lists from there (``StartAfter``).
    ``run``: when the interrupted run's listing started (server time), carried
    over so the watermark a resumed run ends with still covers changes made
    while the first part ran.

    The old cursor was the newest ``LastModified`` seen. Keys are listed by
    name, not by time, so a run cancelled or crashed after its periodic
    commit had already recorded a newer time than objects it had not reached
    yet — they were skipped forever; and an object changed during a run, at a
    time older than another object's, was never picked up.
    """

    since: str = ""
    after: str = ""
    run: str = ""
    # A pre-P1b-3 cursor: the newest LastModified already ingested (exclusive).
    legacy: bool = False

    @classmethod
    def parse(cls, raw: str | None) -> _ListingCursor:
        text = (raw or "").strip()
        if not text:
            return cls()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                return cls()
            return cls(
                since=str(data.get("since") or ""),
                after=str(data.get("after") or ""),
                run=str(data.get("run") or ""),
            )
        return cls(since=text, legacy=True)  # the newest LastModified seen

    def dump(self) -> str:
        return json.dumps(
            {"v": 2, "since": self.since, "after": self.after, "run": self.run},
            separators=(",", ":"),
        )


def _as_utc(value: str) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def _server_time(page: dict[str, Any]) -> datetime.datetime | None:
    """The store's own clock: the HTTP ``Date`` header of a listing response."""
    from email.utils import parsedate_to_datetime

    headers = (page.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
    raw = headers.get("date") or headers.get("Date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(str(raw)).astimezone(datetime.UTC)
    except (TypeError, ValueError):
        return None


def _lookback_seconds(config: SourceConfig) -> int:
    try:
        raw = config.connection_config.get("cursor_lookback_seconds", _DEFAULT_LOOKBACK_SECONDS)
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LOOKBACK_SECONDS
    return max(0, min(value, 7 * 86400))


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
    def _client_kwargs(
        endpoint_url: str | None, config: SourceConfig | None = None
    ) -> dict[str, Any]:
        """boto3 client kwargs.

        A custom endpoint is addressed path-style by default (MinIO and most
        S3-compatible stores); ``connection_config.addressing_style`` may ask for
        ``virtual`` (``<bucket>.<host>``, AWS-style) or ``auto``. Every host boto3
        then resolves — the virtual-hosted name included — is egress-checked by
        the driver runner (:func:`run_driver_call`).
        """
        style = str((config.connection_config if config else {}).get("addressing_style") or "")
        style = style.strip().lower()
        if style and style not in _ADDRESSING_STYLES:
            raise ValueError(
                f"addressing_style must be one of {sorted(_ADDRESSING_STYLES)}, got {style!r}"
            )
        if endpoint_url is None and not style:
            return {}
        from botocore.config import Config  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {"config": Config(s3={"addressing_style": style or "path"})}
        if endpoint_url is not None:
            kwargs["endpoint_url"] = endpoint_url
        return kwargs

    def _keys(self, config: SourceConfig) -> AwsKeys | None:
        """The source's own keys (incl. an STS session token), or None (anonymous).

        A secret this process could not decrypt raises
        :class:`ConnectorSecretsUndecryptableError`, half a key pair / the response
        mask raises :class:`AwsCredentialError`. Either used to come out as
        ``aws_access_key_id=None``, which botocore answers with its DEFAULT
        credential chain — env vars, ~/.aws, then the EC2 metadata service
        (169.254.169.254): the platform pod's identity, never the tenant's.
        """
        refuse_undecryptable_secrets(config)
        return keys_from_config(config.connection_config)

    def _make_client(self, boto3: Any, config: SourceConfig, endpoint_url: str | None) -> Any:
        """An S3 client for the source (listing, single-object fetch, count estimate).

        Signed with the source's own keys, or explicitly UNSIGNED when it has
        none; built on an isolated botocore session that never consults the
        ambient credential chain or instance metadata (:mod:`app.net.aws_clients`).
        The region is always explicit; custom endpoints stay path-style by default.
        """
        client_kwargs = self._client_kwargs(endpoint_url, config)
        return tenant_client(
            boto3,
            "s3",
            region=config.connection_config.get("region"),
            keys=self._keys(config),
            endpoint_url=client_kwargs.get("endpoint_url"),
            config=client_kwargs.get("config"),
        )

    def _object_client(self, boto3: Any, config: SourceConfig, endpoint_url: str | None) -> Any:
        """A one-off client (single-object fetch, count estimate): same rules."""
        return self._make_client(boto3, config, endpoint_url)

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            bucket = config.connection_config.get("bucket", "")
            region = resolve_region(config.connection_config.get("region"))

            async with self._pinned_endpoint(config) as endpoint_url:
                import boto3  # type: ignore[import-not-found]

                def _probe() -> tuple[float, Any]:
                    s3 = self._make_client(boto3, config, endpoint_url)
                    # A listing (what a sync needs) — its error carries the S3
                    # code and message; HEAD Bucket's error has no body, so a bad
                    # key only ever read "403 Forbidden".
                    resp = s3.list_objects_v2(
                        Bucket=bucket,
                        Prefix=config.connection_config.get("prefix", ""),
                        MaxKeys=1,
                    )
                    latency = (time.perf_counter() - t0) * 1000
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
            return ConnectionHealth(ok=False, error=_describe(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Objects changed since the last run, in key order (see :class:`_ListingCursor`).

        Every yielded document carries a cursor that resumes after its key; when
        the listing completes, :attr:`completed_cursor` holds the next run's
        watermark (when this run's listing started, by the store's clock, minus
        the look-back).
        """
        self.completed_cursor: str | None = None
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "boto3 is not installed on this server; the connector cannot run"
            ) from exc

        bucket = config.connection_config.get("bucket", "")
        try:
            async with self._pinned_endpoint(config) as endpoint_url:
                async for raw, new_cursor in self._iter_objects(
                    boto3, endpoint_url, config, cursor=_ListingCursor.parse(cursor)
                ):
                    yield raw, new_cursor
        except (ConnectorUnavailableError, ConnectorFetchError, ConnectorEgressBlockedError):
            raise
        except Exception as exc:
            # USR-1: a listing / auth / endpoint failure fails the sync with the
            # store's own reason (code + message), never a bare exception name.
            _log.error("s3_connector_error bucket=%s: %s", bucket, exc)
            raise ConnectorFetchError(f"{self.source_type}: {_describe(exc)}") from exc

    async def _iter_objects(
        self,
        boto3: Any,
        endpoint_url: str | None,
        config: SourceConfig,
        *,
        cursor: _ListingCursor,
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        bucket = cc.get("bucket", "")
        prefix = cc.get("prefix", "")
        include_patterns, exclude_patterns = config.include_patterns, config.exclude_patterns
        cap = int(config.max_doc_size_bytes)
        since = _as_utc(cursor.since)
        run_started = _as_utc(cursor.run)
        run = self._runner(endpoint_url)

        def _download(key: str) -> tuple[bytes, str]:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read(cap + 1)
            if len(body) > cap:  # replaced by a bigger object since the listing
                raise _OversizedObjectError(f"object exceeds the {cap}-byte size cap")
            return body, response.get("ContentType", "application/octet-stream")

        # boto3 is blocking: client setup, each page and each download run on
        # the SDK pool (see app.ingestion.sdk_executor), never on the loop.
        s3 = await run(self._make_client, boto3, config, endpoint_url)
        params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if cursor.after:
            params["StartAfter"] = cursor.after
        pages = s3.get_paginator("list_objects_v2").paginate(**params)

        async for page in iterate_blocking(pages, chunk_size=1, runner=run):
            if run_started is None:
                run_started = _server_time(page) or datetime.datetime.now(datetime.UTC)
            for obj in page.get("Contents", []):  # lexicographic key order
                key = obj["Key"]
                modified = obj["LastModified"]
                if since is not None and (
                    modified <= since if cursor.legacy else modified < since
                ):
                    continue  # unchanged since the last run's watermark
                if not self._matches_patterns(key, include_patterns, exclude_patterns):
                    continue
                position = dataclasses.replace(
                    cursor, after=key, run=run_started.isoformat(), legacy=False
                ).dump()
                uri = s3_legacy_document_id(bucket, key)
                doc_id = s3_document_id(config, bucket, key)
                meta = {
                    "s3_key": key,
                    "s3_bucket": bucket,
                    "size": obj.get("Size", 0),
                    "etag": str(obj.get("ETag") or "").strip('"'),
                }
                if obj.get("Size", 0) > cap:
                    _log.info("s3_too_large key=%s size=%d", key, obj["Size"])
                    # USR-1: reported (a counted, permanent failure), not dropped.
                    yield fetch_failure_document(
                        config,
                        doc_id=doc_id,
                        reason=f"object exceeds the {cap}-byte size cap",
                        retryable=False,
                        source_url=uri,
                        title=key.split("/")[-1],
                        metadata=meta,
                    ), position
                    continue
                try:
                    content_bytes, content_type = await run(_download, key)
                except ConnectorUnavailableError:
                    raise
                except Exception as e:
                    reason, retryable = _classify_fetch_error(e)
                    _log.warning("s3_download_error key=%s retryable=%s: %s", key, retryable, e)
                    # USR-1/USR-4: a counted failure (→ DLQ); the retry re-fetches
                    # the object (replay_event).
                    yield fetch_failure_document(
                        config,
                        doc_id=doc_id,
                        reason=reason,
                        retryable=retryable,
                        source_url=uri,
                        title=key.split("/")[-1],
                        replay={"kind": _REPLAY_KIND, "bucket": bucket, "key": key},
                        metadata=meta,
                    ), position
                    continue
                yield RawDocument(
                    doc_id=doc_id,
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    content=content_bytes,
                    content_type=content_type,
                    source_url=uri,
                    title=key.split("/")[-1],
                    modified_at=modified.isoformat(),
                    metadata={**meta, CONNECTOR_LEGACY_DOC_ID_KEY: uri},
                ), position

        if run_started is None:  # an empty listing still has a start
            run_started = datetime.datetime.now(datetime.UTC)
        watermark = run_started - datetime.timedelta(seconds=_lookback_seconds(config))
        if since is not None and since > watermark:
            watermark = since  # never move backwards
        self.completed_cursor = _ListingCursor(since=watermark.isoformat()).dump()

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Stream every object key under the configured prefix/patterns (KB-44).

        ListObjectsV2 pages (1,000 keys each) are pulled one at a time on the SDK
        pool and yielded as they arrive — the whole bucket is never held in
        memory. A listing error propagates (never a partial "complete" set).
        """
        import boto3

        cc = config.connection_config
        bucket = cc.get("bucket", "")
        prefix = cc.get("prefix", "")
        async with self._pinned_endpoint(config) as endpoint_url:
            run = self._runner(endpoint_url)
            client = await run(self._make_client, boto3, config, endpoint_url)
            pages = client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
            async for page in iterate_blocking(pages, chunk_size=1, runner=run):
                include, exclude = config.include_patterns, config.exclude_patterns
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if self._matches_patterns(key, include, exclude):
                        yield s3_document_id(config, bucket, key)
                        # A not-yet-migrated copy of a live object is kept.
                        yield s3_legacy_document_id(bucket, key)

    async def list_live_doc_ids(self, config: SourceConfig) -> set[str] | None:
        """The live listing as a set (small buckets / tests; reconciliation streams)."""
        return {doc_id async for doc_id in self.iter_live_doc_ids(config)}

    def manages_doc_id(self, doc_id: str) -> bool:
        """Current ids (Source-scoped uuid5) and legacy ``s3://bucket/key`` ids.

        Reconciliation only asks about documents attributed to this Source.
        """
        import uuid

        text = str(doc_id)
        if text.startswith("s3://"):
            return True
        try:
            return uuid.UUID(text).version == 5
        except ValueError:
            return False

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

    async def replay_event(
        self, config: SourceConfig, reference: dict[str, Any]
    ) -> AsyncIterator[RawDocument]:
        """Fetch again the object a failed S3 event named (DLQ retry).

        Yields the object, or a fresh failure document (reason + retryability)
        when it still cannot be read.
        """
        bucket = str(reference.get("bucket") or "")
        key = str(reference.get("key") or "")
        if reference.get("kind") != _REPLAY_KIND or not bucket or not key:
            raise ValueError(f"not an S3 object replay reference: {reference!r}")
        async for raw, _key in self._fetch_single(config, bucket, key):
            yield raw

    async def _fetch_single(
        self, config: SourceConfig, bucket: str, key: str
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Fetch and yield a single S3 object.

        A fetch that fails yields a failure document instead (reason + whether a
        retry can help, see :func:`_classify_fetch_error`): the pipeline fails it
        with that reason (→ DLQ). It used to be logged and dropped, so the event
        simply vanished.
        """
        from app.ingestion.source_config import (
            CONNECTOR_FAILURE_KEY,
            CONNECTOR_FAILURE_RETRYABLE_KEY,
            CONNECTOR_REPLAY_KEY,
            RawDocument,
        )

        cap = int(config.max_doc_size_bytes)

        legacy_id = s3_legacy_document_id(bucket, key)

        def _doc(content: bytes, content_type: str, **meta: Any) -> RawDocument:
            return RawDocument(
                doc_id=s3_document_id(config, bucket, key),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                content=content,
                content_type=content_type,
                source_url=legacy_id,
                title=key.split("/")[-1],
                metadata={
                    "s3_key": key,
                    "s3_bucket": bucket,
                    CONNECTOR_LEGACY_DOC_ID_KEY: legacy_id,
                    **meta,
                },
            )

        try:
            import boto3

            async with self._pinned_endpoint(config) as endpoint_url:

                def _fetch() -> tuple[bytes, str]:
                    s3 = self._object_client(boto3, config, endpoint_url)
                    response = s3.get_object(Bucket=bucket, Key=key)
                    size = response.get("ContentLength")
                    if isinstance(size, int) and size > cap:
                        response["Body"].close()
                        raise _OversizedObjectError(f"object exceeds the {cap}-byte size cap")
                    body = response["Body"].read(cap + 1)
                    if len(body) > cap:
                        raise _OversizedObjectError(f"object exceeds the {cap}-byte size cap")
                    return body, response.get("ContentType", "application/octet-stream")

                content_bytes, content_type = await self._runner(endpoint_url)(_fetch)
        except ConnectorUnavailableError:
            raise
        except Exception as exc:
            reason, retryable = _classify_fetch_error(exc)
            _log.warning(
                "s3_webhook_fetch_failed bucket=%s key=%s retryable=%s: %s",
                bucket,
                key,
                retryable,
                reason,
            )
            failure = {
                CONNECTOR_FAILURE_KEY: reason,
                CONNECTOR_FAILURE_RETRYABLE_KEY: retryable,
                # Stored with the DLQ entry: a retry re-fetches this object
                # (replay_event) instead of replaying the empty failure document.
                CONNECTOR_REPLAY_KEY: {"kind": _REPLAY_KIND, "bucket": bucket, "key": key},
            }
            yield _doc(b"", "application/octet-stream", **failure), key
            return
        yield _doc(content_bytes, content_type), key

    def estimate_doc_count(self, config: SourceConfig) -> int | None:
        try:
            import boto3

            with self._pinned_endpoint_sync(config) as endpoint_url:
                s3 = self._object_client(boto3, config, endpoint_url)
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
