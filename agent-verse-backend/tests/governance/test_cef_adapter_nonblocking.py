"""AUDIT-03: the CEF SIEM adapter never blocks the event loop.

``CEFAdapter.send`` did a blocking ``socket.connect``/``sendall`` (5 s timeout per
call) inside the coroutine, stalling every other request on that loop. The
socket work now runs in a worker thread.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.governance.siem_adapters import CEFAdapter, SIEMConfig


class _FakeSocket:
    calls: list[tuple[str, str]] = []

    def __init__(self, *_a: Any) -> None:
        pass

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def settimeout(self, _t: float) -> None:
        pass

    def connect(self, _addr: Any) -> None:
        _FakeSocket.calls.append(("connect", threading.current_thread().name))

    def sendall(self, _data: bytes) -> None:
        _FakeSocket.calls.append(("sendall", threading.current_thread().name))


async def test_socket_io_runs_off_the_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    _FakeSocket.calls = []
    monkeypatch.setattr(socket, "socket", _FakeSocket)
    loop_thread = threading.current_thread().name
    ok = await CEFAdapter().send(
        [{"event_type": "x", "action": "y"}],
        SIEMConfig(host="siem.example", port=514, protocol="tcp"),
    )
    assert ok is True
    assert [c[0] for c in _FakeSocket.calls] == ["connect", "sendall"]
    assert all(thread != loop_thread for _, thread in _FakeSocket.calls)


async def test_socket_error_is_reported_false(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    class _Down(_FakeSocket):
        def connect(self, _addr: Any) -> None:
            raise ConnectionRefusedError("down")

    monkeypatch.setattr(socket, "socket", _Down)
    ok = await CEFAdapter().send([{}], SIEMConfig(host="h", port=1, protocol="tcp"))
    assert ok is False
