"""MCPWebSocketClient — WebSocket transport for real-time MCP tool servers.

Used for connectors that push data (stock feeds, IoT, live dashboards).
Supports:
  - Subscribe to streaming tool outputs
  - Handle bidirectional WebSocket tool calls
  - Reconnection with exponential backoff
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

_RECONNECT_BASE = 1.0    # seconds
_RECONNECT_MAX = 60.0    # seconds
_RECONNECT_JITTER = 0.1


class MCPWebSocketClient:
    """Async WebSocket client for MCP connectors that support WS transport.

    Usage::

        client = MCPWebSocketClient("ws://iot-connector:8080/ws")
        async with client.connect() as session:
            async for event in session.subscribe("temperature_feed"):
                print(event)
    """

    def __init__(
        self,
        ws_url: str,
        auth_token: str | None = None,
        reconnect: bool = True,
        max_reconnects: int = 10,
    ) -> None:
        self._ws_url = ws_url
        self._auth_token = auth_token
        self._reconnect = reconnect
        self._max_reconnects = max_reconnects
        self._ws: Any | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._subscriptions: dict[str, asyncio.Queue[Any]] = {}
        self._msg_id = 0
        self._connected = False

    # ── Connection management ──────────────────────────────────────────────────

    async def connect(self) -> "MCPWebSocketClient":
        """Open WebSocket connection. Returns self for use as async context manager."""
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "websockets is not installed. Install with: pip install websockets"
            )

        headers = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        self._ws = await websockets.connect(self._ws_url, extra_headers=headers)
        self._connected = True
        logger.info("MCPWebSocketClient connected to %s", self._ws_url)
        # Start message dispatcher
        asyncio.create_task(self._dispatch_messages())
        return self

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "MCPWebSocketClient":
        return await self.connect()

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ── Message dispatcher ─────────────────────────────────────────────────────

    async def _dispatch_messages(self) -> None:
        """Background task — reads WebSocket messages and routes to waiters."""
        reconnects = 0
        while self._connected and reconnects <= self._max_reconnects:
            try:
                async for raw_msg in self._ws:  # type: ignore[union-attr]
                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON WS message: %s", raw_msg[:100])
                        continue

                    msg_id: str | None = msg.get("id")
                    msg_type: str = msg.get("type", "")
                    channel: str = msg.get("channel", "")

                    # Route RPC responses to pending futures
                    if msg_id and msg_id in self._pending:
                        future = self._pending.pop(msg_id)
                        if "error" in msg:
                            future.set_exception(RuntimeError(msg["error"]))
                        else:
                            future.set_result(msg.get("result"))

                    # Route subscription events to queues
                    elif channel and channel in self._subscriptions:
                        await self._subscriptions[channel].put(msg.get("data"))

                    elif msg_type == "ping":
                        await self._ws.send(  # type: ignore[union-attr]
                            json.dumps({"type": "pong"})
                        )

                reconnects = 0  # reset on clean exit

            except Exception as exc:
                logger.warning("WS connection lost: %s", exc)
                if not self._reconnect or reconnects >= self._max_reconnects:
                    break
                reconnects += 1
                delay = min(
                    _RECONNECT_BASE * (2 ** reconnects) + _RECONNECT_JITTER,
                    _RECONNECT_MAX,
                )
                logger.info(
                    "Reconnecting in %.1fs (attempt %d/%d)",
                    delay,
                    reconnects,
                    self._max_reconnects,
                )
                await asyncio.sleep(delay)
                try:
                    await self.connect()
                except Exception as conn_exc:
                    logger.warning("Reconnect failed: %s", conn_exc)

    # ── RPC (request/response) ─────────────────────────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> Any:
        """Call a tool via WebSocket RPC and await the result.

        Args:
            tool_name: MCP tool name.
            arguments: Tool input arguments.
            timeout: Seconds to wait for a response.

        Returns:
            Tool output (parsed JSON).
        """
        self._msg_id += 1
        msg_id = str(self._msg_id)
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        await self._ws.send(  # type: ignore[union-attr]
            json.dumps({
                "id": msg_id,
                "type": "call_tool",
                "tool": tool_name,
                "arguments": arguments,
            })
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(
                f"WebSocket tool call '{tool_name}' timed out after {timeout}s"
            )

    # ── Streaming subscriptions ────────────────────────────────────────────────

    async def subscribe(
        self,
        channel: str,
        params: dict[str, Any] | None = None,
        buffer_size: int = 100,
    ) -> AsyncIterator[Any]:
        """Subscribe to a real-time event channel.

        Yields data payloads from the channel as they arrive.

        Args:
            channel: Channel / feed name (e.g. "temperature_feed", "stock.AAPL").
            params: Optional subscription parameters.
            buffer_size: Internal queue buffer size.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=buffer_size)
        self._subscriptions[channel] = queue

        self._msg_id += 1
        await self._ws.send(  # type: ignore[union-attr]
            json.dumps({
                "id": str(self._msg_id),
                "type": "subscribe",
                "channel": channel,
                "params": params or {},
            })
        )

        try:
            while True:
                data = await queue.get()
                if data is None:  # None = unsubscribe signal
                    break
                yield data
        finally:
            self._subscriptions.pop(channel, None)
            try:
                self._msg_id += 1
                await self._ws.send(  # type: ignore[union-attr]
                    json.dumps({
                        "id": str(self._msg_id),
                        "type": "unsubscribe",
                        "channel": channel,
                    })
                )
            except Exception:
                pass

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools on the WS MCP server."""
        return await self.call_tool("__list_tools__", {})
