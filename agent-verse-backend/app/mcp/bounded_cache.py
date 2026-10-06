"""A small bounded, expiring in-process cache (a02-F030-11).

``MCPClient`` kept its circuit breakers and its tool-schema cache in plain dicts
keyed by tenant x server: one long-lived API process grew by one entry per
connector any tenant ever called, forever, and a cached schema outlived every
change to its connector. This cache holds at most ``maxsize`` entries (least
recently used goes first) and drops an entry ``ttl_s`` seconds after it was
written (``sliding=True``: after it was last used).

It supports the dict operations the callers (and their tests) use: ``get``,
``[]``, ``[]=``, ``in``, ``len``, ``pop`` and ``clear``.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable

_MISSING = object()


class BoundedTTLCache[K: Hashable, V]:
    """LRU-bounded mapping whose entries expire ``ttl_s`` after write (or last use)."""

    def __init__(
        self,
        *,
        maxsize: int,
        ttl_s: float,
        sliding: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maxsize = max(1, int(maxsize))
        self.ttl_s = float(ttl_s)
        self.sliding = sliding
        self._clock = clock
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()

    def _live(self, key: K) -> object:
        item = self._data.get(key)
        if item is None:
            return _MISSING
        value, stamp = item
        now = self._clock()
        if now - stamp > self.ttl_s:
            del self._data[key]
            return _MISSING
        self._data.move_to_end(key)
        if self.sliding:
            self._data[key] = (value, now)
        return value

    def get(self, key: K, default: V | None = None) -> V | None:
        value = self._live(key)
        return default if value is _MISSING else value  # type: ignore[return-value]

    def __getitem__(self, key: K) -> V:
        value = self._live(key)
        if value is _MISSING:
            raise KeyError(key)
        return value  # type: ignore[return-value]

    def __setitem__(self, key: K, value: V) -> None:
        self._data[key] = (value, self._clock())
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        return self._live(key) is not _MISSING  # type: ignore[arg-type]

    def __len__(self) -> int:
        return len(self._data)

    def pop(self, key: K, default: V | None = None) -> V | None:
        item = self._data.pop(key, None)
        return default if item is None else item[0]

    def clear(self) -> None:
        self._data.clear()


__all__ = ["BoundedTTLCache"]
