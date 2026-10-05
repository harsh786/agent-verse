"""ElasticsearchConnector — Elasticsearch / OpenSearch index ingestion.

Connection config (Sources -> NoSQL Database -> elasticsearch / opensearch):

    url                  Cluster URL (``http(s)://host:9200``), egress-checked.
    index                Index, alias, data stream, comma list or pattern
                         (``logs-*``); every concrete index it covers is read
                         (default ``_all``: every open index).
    username, password   Basic authentication, or
    api_key              an API key: the ``encoded`` value Elasticsearch returns
                         (base64 of ``id:key``) or ``id:key`` itself.
    auth_mode            ``basic`` / ``api_key`` / ``none`` (the Sources form sets it);
                         without it an ``api_key`` wins over a username.
    sort_field           Field ordering the incremental sync (default
                         ``@timestamp``): a date (or numeric) field the index maps,
                         typically an update timestamp. ``_doc`` reads everything
                         each sync (no ordering field; unchanged documents are
                         skipped by the pipeline's dedup).
    cursor_lookback_seconds  Date sort fields: each sync re-reads this window
                         before the last value seen (default 60), so a document
                         refreshed late or written with a slightly older timestamp
                         is not missed. Re-read unchanged documents are skipped.
    batch_size           Documents per page (default 500, at most 10,000).

Paging: a point in time (PIT) with ``search_after`` on ``[sort_field,
_shard_doc]`` — a consistent snapshot, no 10,000-hit limit, and no sort on
``_id`` (Elasticsearch 8 refuses field data on ``_id``). Where PIT is not
available (OpenSearch, older clusters) the scroll API is used instead.

Cursor: ``{"v": 2, "field": sort_field, "since": value}`` — the highest
``sort_field`` value of a document read. The next sync reads documents with
``sort_field >= since - lookback``. A document without the field is read by the
first sync (and every ``_doc`` sync) but never moves the cursor (it used to sort
last with a sentinel that became the cursor, after which no later sync read
anything). Updates are seen when the update also moves ``sort_field``.

Documents are keyed by Source + concrete index + ``_id`` (equal ``_id`` values in
two indices of a pattern are two documents). Upstream deletions are removed by
reconciliation (``iter_live_doc_ids`` lists every document of the index).
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ensure_success,
    stable_doc_id,
)
from app.ingestion.connector_egress import assert_source_url, source_client
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_DEFAULT_BATCH = 500
_MAX_BATCH = 10_000
_DEFAULT_LOOKBACK_S = 60
_KEEP_ALIVE = "2m"
_LISTING_PAGE = 1000
_NO_FIELD = "_doc"


@dataclass(frozen=True)
class _Settings:
    url: str
    index: str
    sort_field: str
    batch_size: int
    lookback_s: int
    auth: tuple[str, str] | None
    headers: dict[str, str]

    @property
    def ordered(self) -> bool:
        return self.sort_field != _NO_FIELD


def _settings(cc: dict[str, Any]) -> _Settings:
    url = str(cc.get("url") or "").strip().rstrip("/")
    if not url:
        raise ValueError("Elasticsearch source needs the cluster url")
    # No index: every open index (the historical default).
    index = str(cc.get("index") or "").strip() or "_all"
    headers: dict[str, str] = {}
    auth: tuple[str, str] | None = None
    # The Sources form sends auth_mode; without it, an API key wins over basic.
    mode = str(cc.get("auth_mode") or "").strip().lower()
    api_key = str(cc.get("api_key") or "").strip() if mode in ("", "api_key") else ""
    if mode == "api_key" and not api_key:
        raise ValueError("Elasticsearch auth_mode 'api_key' needs an api_key")
    if mode == "basic" and not cc.get("username"):
        raise ValueError("Elasticsearch auth_mode 'basic' needs a username")
    if api_key:
        if ":" in api_key:  # "id:key" -> the encoded form
            api_key = base64.b64encode(api_key.encode()).decode()
        headers["Authorization"] = f"ApiKey {api_key}"
    elif cc.get("username") and mode in ("", "basic"):
        auth = (str(cc.get("username")), str(cc.get("password") or ""))
    batch = int(cc.get("batch_size") or _DEFAULT_BATCH)
    raw_lookback = cc.get("cursor_lookback_seconds")
    lookback = _DEFAULT_LOOKBACK_S if raw_lookback in (None, "") else int(str(raw_lookback))
    return _Settings(
        url=url,
        index=index,
        sort_field=str(cc.get("sort_field") or "@timestamp").strip(),
        batch_size=max(1, min(batch, _MAX_BATCH)),
        lookback_s=max(0, lookback),
        auth=auth,
        headers=headers,
    )


def _kwargs(s: _Settings, **extra: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(extra)
    if s.auth is not None:
        kw["auth"] = s.auth
    if s.headers:
        kw["headers"] = {**s.headers, **kw.get("headers", {})}
    return kw


def _index_path(index: str) -> str:
    return quote(index, safe="*,-_.")


async def _field_type(client: Any, s: _Settings) -> str:
    """The sort field's mapped type across the indices, or raise when none maps it."""
    r = await client.get(
        f"{s.url}/{_index_path(s.index)}/_mapping/field/{quote(s.sort_field, safe='@._-')}",
        **_kwargs(s),
    )
    ensure_success(r, source_type="elasticsearch", what=f"mapping of {s.index!r}")
    types: set[str] = set()
    for body in (r.json() or {}).values():
        for spec in ((body or {}).get("mappings") or {}).values():
            mapping = (spec or {}).get("mapping") or {}
            for leaf in mapping.values():
                if isinstance(leaf, dict) and leaf.get("type"):
                    types.add(str(leaf["type"]))
    if not types:
        raise ConnectorFetchError(
            f"elasticsearch: sort_field {s.sort_field!r} is not mapped in any index of "
            f"{s.index!r}; set sort_field to a date or numeric field the index maps "
            f"(an update timestamp), or to '_doc' to read every document each sync"
        )
    if "date" in types:
        return "date"
    if "date_nanos" in types:
        return "date_nanos"
    return sorted(types)[0]


def _decode_cursor(cursor: str | None, s: _Settings) -> Any:
    """The ``since`` value of a stored cursor (None: read from the start)."""
    if not cursor or not s.ordered:
        return None
    try:
        data = json.loads(cursor)
    except ValueError:
        return None
    if isinstance(data, dict):
        return data.get("since") if data.get("field") == s.sort_field else None
    if isinstance(data, list) and data:  # legacy: the last hit's [sort value, _id]
        return data[0]
    return None


def _encode_cursor(s: _Settings, since: Any) -> str:
    return json.dumps({"v": 2, "field": s.sort_field, "since": since})


def _dotted(source: dict[str, Any], path: str) -> Any:
    value: Any = source
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _sort(s: _Settings, tiebreak: str, field_type: str = "") -> list[Any]:
    if not s.ordered:
        return [tiebreak]
    spec: dict[str, Any] = {"order": "asc", "missing": "_last"}
    if field_type:
        # Indices of a pattern that do not map the field sort it as missing
        # instead of failing the whole search.
        spec["unmapped_type"] = field_type
    return [{s.sort_field: spec}, tiebreak]


def _query(s: _Settings, since: Any, field_type: str) -> dict[str, Any]:
    if since is None or not s.ordered:
        return {"match_all": {}}
    if field_type in ("date", "date_nanos") and isinstance(since, int | float):
        millis = int(since) // 1_000_000 if field_type == "date_nanos" else int(since)
        start = millis - s.lookback_s * 1000
        return {"range": {s.sort_field: {"gte": start, "format": "epoch_millis"}}}
    return {"range": {s.sort_field: {"gte": since}}}


def _later(value: Any, since: Any) -> bool:
    if since is None:
        return True
    try:
        return bool(value >= since)
    except TypeError:  # a legacy cursor of another type
        return True


async def _hits(
    client: Any, s: _Settings, query: dict[str, Any], *, source: bool = True,
    size: int | None = None, field_type: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Every hit of ``query`` in sort order: PIT + search_after, else scroll."""
    size = size or s.batch_size
    pit = await client.post(
        f"{s.url}/{_index_path(s.index)}/_pit", params={"keep_alive": _KEEP_ALIVE}, **_kwargs(s)
    )
    if pit.status_code in (401, 403) or (
        pit.status_code == 404 and "index_not_found" in pit.text
    ):
        # Credentials / privileges / a missing index: the answer a search would
        # give too (opening a PIT needs only the read privilege).
        ensure_success(pit, source_type="elasticsearch", what=f"search of {s.index!r}")
    if pit.is_success:
        pit_id = pit.json().get("id")
        try:
            after: list[Any] | None = None
            while True:
                body: dict[str, Any] = {
                    "size": size,
                    "query": query,
                    "sort": _sort(s, "_shard_doc", field_type),
                    "pit": {"id": pit_id, "keep_alive": _KEEP_ALIVE},
                    "track_total_hits": False,
                    "_source": source,
                }
                if after is not None:
                    body["search_after"] = after
                r = await client.post(f"{s.url}/_search", json=body, **_kwargs(s))
                ensure_success(r, source_type="elasticsearch", what=f"search of {s.index!r}")
                data = r.json()
                pit_id = data.get("pit_id") or pit_id
                hits = (data.get("hits") or {}).get("hits") or []
                for hit in hits:
                    yield hit
                if len(hits) < size:
                    return
                after = hits[-1].get("sort")
        finally:
            try:
                await client.request("DELETE", f"{s.url}/_pit", json={"id": pit_id},
                                     **_kwargs(s))
            except Exception as exc:  # the PIT expires on its own
                _log.debug("elasticsearch_pit_close_failed: %s", exc)
        return
    # No PIT here (OpenSearch / older clusters): a scroll is a consistent snapshot too.
    _log.info("elasticsearch_pit_unavailable index=%s status=%s — using scroll",
              s.index, pit.status_code)
    r = await client.post(
        f"{s.url}/{_index_path(s.index)}/_search",
        params={"scroll": _KEEP_ALIVE},
        json={"size": size, "query": query, "sort": _sort(s, "_doc", field_type),
              "_source": source},
        **_kwargs(s),
    )
    ensure_success(r, source_type="elasticsearch", what=f"search of {s.index!r}")
    data = r.json()
    scroll_id = data.get("_scroll_id")
    try:
        while True:
            hits = (data.get("hits") or {}).get("hits") or []
            for hit in hits:
                yield hit
            if not hits or not scroll_id:
                return
            r = await client.post(f"{s.url}/_search/scroll",
                                  json={"scroll": _KEEP_ALIVE, "scroll_id": scroll_id},
                                  **_kwargs(s))
            ensure_success(r, source_type="elasticsearch", what=f"scroll of {s.index!r}")
            data = r.json()
            scroll_id = data.get("_scroll_id") or scroll_id
    finally:
        if scroll_id:
            try:
                await client.request("DELETE", f"{s.url}/_search/scroll",
                                     json={"scroll_id": scroll_id}, **_kwargs(s))
            except Exception as exc:
                _log.debug("elasticsearch_scroll_close_failed: %s", exc)


@register("elasticsearch", feature_flag="ingestion_connector_elasticsearch_enabled")
@register("opensearch")
class ElasticsearchConnector(BaseConnector):
    """Elasticsearch / OpenSearch connector — index-based ingestion."""

    source_type = "elasticsearch"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        t0 = time.perf_counter()
        try:
            s = _settings(config.connection_config)
            assert_source_url(s.url, context="elasticsearch", config=config)
            meta: dict[str, Any] = {"index": s.index}
            async with source_client(timeout=10) as client:
                root = await client.get(s.url, **_kwargs(s))
                if root.status_code == 401:
                    ensure_success(root, source_type="elasticsearch", what="authentication")
                if root.is_success:  # a reader without cluster 'monitor' gets 403 here
                    info = root.json()
                    meta.update(version=(info.get("version") or {}).get("number"),
                                cluster=info.get("cluster_name"))
                count = await client.get(f"{s.url}/{_index_path(s.index)}/_count",
                                         **_kwargs(s))
                ensure_success(count, source_type="elasticsearch",
                               what=f"access to {s.index!r}")
                meta["documents"] = int(count.json().get("count") or 0)
                if s.ordered:
                    meta["sort_field_type"] = await _field_type(client, s)
            return ConnectionHealth(ok=True, latency_ms=(time.perf_counter() - t0) * 1000,
                                    metadata=meta)
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc) or type(exc).__name__)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        s = _settings(config.connection_config)
        assert_source_url(s.url, context="elasticsearch", config=config)
        since = _decode_cursor(cursor, s)
        async with source_client(timeout=60) as client:
            field_type = await _field_type(client, s) if s.ordered else ""
            query = _query(s, since, field_type)
            async for hit in _hits(client, s, query, field_type=field_type):
                source = hit.get("_source") or {}
                index, doc_key = str(hit.get("_index") or s.index), str(hit.get("_id"))
                # A document without the field sorts last with a sentinel value:
                # it must never become the cursor.
                if s.ordered and _dotted(source, s.sort_field) is not None:
                    value = (hit.get("sort") or [None])[0]
                    if value is not None and _later(value, since):
                        since = value
                doc = RawDocument(
                    doc_id=stable_doc_id(config, index, doc_key),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"{s.url}/{index}/_doc/{quote(doc_key, safe='')}",
                    content=json.dumps(source, ensure_ascii=False, indent=2,
                                       default=str).encode(),
                    content_type="application/json",
                    metadata={"index": index, "_id": doc_key},
                )
                yield doc, _encode_cursor(s, since)

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Every document id upstream (KB-44 deletions): ``_index`` + ``_id`` of each
        document, paged through a PIT (or scroll); any error propagates."""
        s = _settings(config.connection_config)
        assert_source_url(s.url, context="elasticsearch", config=config)
        unordered = dataclasses.replace(s, sort_field=_NO_FIELD)
        async with source_client(timeout=60) as client:
            async for hit in _hits(client, unordered, {"match_all": {}}, source=False,
                                   size=_LISTING_PAGE):
                yield stable_doc_id(config, str(hit.get("_index")), str(hit.get("_id")))

