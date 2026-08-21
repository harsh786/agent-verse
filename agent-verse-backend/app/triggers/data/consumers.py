"""Data trigger consumers — DB row change, S3 events, API poll, RSS feed."""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


class DBRowChangeConsumer:
    """Listen on pg_notify for DB_ROW_CHANGE triggers."""

    TRIGGER_TYPE = "db_row_change"

    def __init__(self, *, trigger_store: Any = None, dispatcher: Any = None) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher

    async def handle_notification(
        self,
        table: str,
        operation: str,
        row: dict,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Process a pg_notify event and dispatch matching triggers."""
        if self._store is None or self._dispatcher is None:
            return []

        triggers = await self._store.find_by_type_async("db_row_change", tenant_id=tenant_id)
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        fired = []
        payload = {"table": table, "operation": operation, "row": row}

        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            # Filter by table
            watch_table = getattr(spec, "db_table", "") or trigger.get("db_table", "")
            if watch_table and watch_table != table:
                continue
            # Filter by operation (INSERT, UPDATE, DELETE)
            watch_op = getattr(spec, "db_operation", "") or trigger.get("db_operation", "")
            if watch_op and watch_op.upper() != operation.upper():
                continue
            try:
                r = await self._dispatcher.dispatch(spec, payload, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("db_row_change_dispatch_error: %s", exc)
        return fired


class S3EventConsumer:
    """Handle S3 event notifications for S3_EVENT triggers."""

    TRIGGER_TYPE = "s3_event"

    def __init__(self, *, trigger_store: Any = None, dispatcher: Any = None) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher

    async def handle(
        self,
        bucket: str,
        key: str,
        event_type: str,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Process an S3 event (e.g. ObjectCreated, ObjectRemoved)."""
        if self._store is None or self._dispatcher is None:
            return []

        triggers = await self._store.find_by_type_async("s3_event", tenant_id=tenant_id)
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        payload = {"bucket": bucket, "key": key, "event_type": event_type}
        fired = []

        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            watch_bucket = getattr(spec, "s3_bucket", "") or ""
            watch_prefix = getattr(spec, "s3_prefix", "") or ""
            if watch_bucket and watch_bucket != bucket:
                continue
            if watch_prefix and not key.startswith(watch_prefix):
                continue
            try:
                r = await self._dispatcher.dispatch(spec, payload, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("s3_event_dispatch_error: %s", exc)
        return fired


class APIPoller:
    """Generic HTTP API poller for API_POLL triggers."""

    TRIGGER_TYPE = "api_poll"

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
        http_client: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._http = http_client

    async def poll_trigger(self, trigger: dict, *, tenant_id: str, plan: str = "free") -> Any:
        """Execute a single poll cycle for a trigger."""
        spec = trigger.get("spec", trigger)
        url = getattr(spec, "poll_url", "") or ""
        method = getattr(spec, "poll_method", "GET") or "GET"

        if not url:
            return None

        # Execute HTTP request
        response_data: dict = {}
        if self._http is not None:
            try:
                resp = await self._http.request(method, url)
                response_data = await resp.json() if callable(getattr(resp, "json", None)) else {}
            except Exception as exc:
                _log.warning("api_poll_request_error url=%s: %s", url, exc)
                return None

        payload = {"url": url, "method": method, "response": response_data}

        # JSONPath extraction
        jsonpath = getattr(spec, "poll_jsonpath", "") or ""
        expected = getattr(spec, "poll_expected_value", "") or ""
        if jsonpath and response_data:
            try:
                value = self._extract_jsonpath(response_data, jsonpath)
                if expected and str(value) != str(expected):
                    return None  # condition not met
                payload["extracted_value"] = str(value)
            except Exception:
                pass

        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        try:
            return await self._dispatcher.dispatch(spec, payload, tenant_ctx)
        except Exception as exc:
            _log.warning("api_poll_dispatch_error: %s", exc)
            return None

    def _extract_jsonpath(self, data: dict, path: str) -> Any:
        """Simple dot-notation JSONPath: $.foo.bar → data['foo']['bar']."""
        parts = path.lstrip("$.").split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


class RSSPoller:
    """Poll RSS/Atom feeds for RSS_FEED triggers."""

    TRIGGER_TYPE = "rss_feed"

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
        http_client: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._http = http_client
        self._seen_entries: dict[str, set] = {}  # trigger_id → set of entry IDs

    async def check_feed(
        self,
        trigger: dict,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Check an RSS feed for new entries and dispatch for each new one."""
        spec = trigger.get("spec", trigger)
        url = getattr(spec, "rss_url", "") or ""
        if not url or self._http is None:
            return []

        schedule_id = trigger.get("schedule_id", "default")
        seen = self._seen_entries.setdefault(schedule_id, set())

        # Fetch feed
        try:
            resp = await self._http.get(url)
            content = await resp.text() if callable(getattr(resp, "text", None)) else ""
        except Exception as exc:
            _log.warning("rss_fetch_error url=%s: %s", url, exc)
            return []

        # Parse feed (basic)
        entries = self._parse_feed(content)
        fired = []
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)

        for entry in entries:
            entry_id = entry.get("id") or entry.get("link", "")
            if entry_id in seen:
                continue
            seen.add(entry_id)
            try:
                r = await self._dispatcher.dispatch(spec, entry, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("rss_dispatch_error: %s", exc)
        return fired

    def _parse_feed(self, content: str) -> list[dict]:
        """Simple RSS parser — returns list of entry dicts."""
        entries = []
        import re

        # Match <item> or <entry> elements
        pattern = re.compile(r"<(?:item|entry)>(.*?)</(?:item|entry)>", re.DOTALL)
        for match in pattern.finditer(content):
            item_xml = match.group(1)
            entry: dict = {}
            for tag in ("title", "link", "id", "description", "summary", "pubDate", "updated"):
                tag_match = re.search(rf"<{tag}>(.*?)</{tag}>", item_xml, re.DOTALL)
                if tag_match:
                    entry[tag] = tag_match.group(1).strip()
            if entry:
                entries.append(entry)
        return entries
