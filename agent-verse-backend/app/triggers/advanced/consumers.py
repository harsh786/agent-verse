"""Advanced trigger consumers — GraphQL subscriptions, WebSocket messages, price polling."""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


class GraphQLSubscriptionConsumer:
    """Maintain a WebSocket connection to a GraphQL endpoint
    and fire triggers on subscription events.
    """

    TRIGGER_TYPE = "graphql_subscription"

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
        ws_factory: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._ws_factory = ws_factory
        self._connections: dict = {}

    async def handle_message(
        self,
        endpoint: str,
        data: dict,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Process a GraphQL subscription message and dispatch triggers."""
        if self._store is None or self._dispatcher is None:
            return []

        triggers = await self._store.find_by_type_async("graphql_subscription", tenant_id=tenant_id)
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        fired = []
        payload = {"endpoint": endpoint, "data": data}

        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            watch_ep = getattr(spec, "graphql_endpoint", "") or ""
            if watch_ep and watch_ep != endpoint:
                continue
            try:
                r = await self._dispatcher.dispatch(spec, payload, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("graphql_sub_dispatch_error: %s", exc)
        return fired


class WebSocketMessageConsumer:
    """Persistent WebSocket connection that fires triggers on matching messages."""

    TRIGGER_TYPE = "websocket_message"

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher

    async def handle_message(
        self,
        url: str,
        message: str,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Process a WebSocket message and dispatch matching triggers."""
        if self._store is None or self._dispatcher is None:
            return []

        triggers = await self._store.find_by_type_async("websocket_message", tenant_id=tenant_id)
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        fired = []
        payload = {"url": url, "message": message}

        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            watch_url = getattr(spec, "websocket_url", "") or ""
            if watch_url and watch_url != url:
                continue
            # Pattern matching
            pattern = getattr(spec, "websocket_message_pattern", "") or ""
            if pattern:
                import re

                if not re.search(pattern, message):
                    continue
            try:
                r = await self._dispatcher.dispatch(spec, payload, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("websocket_dispatch_error: %s", exc)
        return fired


class PriceThresholdPoller:
    """Poll price data and fire triggers when threshold is crossed."""

    TRIGGER_TYPE = "price_threshold"

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
        price_client: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._price_client = price_client
        self._last_prices: dict = {}  # symbol → last_price

    async def check_price(
        self,
        symbol: str,
        current_price: float,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Check if price crosses threshold and dispatch matching triggers."""
        if self._store is None or self._dispatcher is None:
            return []

        triggers = await self._store.find_by_type_async("price_threshold", tenant_id=tenant_id)
        from types import SimpleNamespace

        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        fired = []
        last = self._last_prices.get(symbol)

        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            watch_symbol = getattr(spec, "price_symbol", "") or ""
            if watch_symbol and watch_symbol.upper() != symbol.upper():
                continue
            threshold = getattr(spec, "price_threshold", None)
            direction = getattr(spec, "price_direction", "above") or "above"
            if threshold is None:
                continue

            crossed = False
            if direction == "above" and current_price >= threshold:
                if last is None or last < threshold:
                    crossed = True
            elif direction == "below" and current_price <= threshold:
                if last is None or last > threshold:
                    crossed = True
            elif direction == "either":
                crossed = True

            if not crossed:
                continue

            payload = {
                "symbol": symbol,
                "current_price": current_price,
                "threshold": threshold,
                "direction": direction,
                "last_price": last,
            }
            try:
                r = await self._dispatcher.dispatch(spec, payload, tenant_ctx)
                fired.append(r)
            except Exception as exc:
                _log.warning("price_threshold_dispatch_error: %s", exc)

        self._last_prices[symbol] = current_price
        return fired
