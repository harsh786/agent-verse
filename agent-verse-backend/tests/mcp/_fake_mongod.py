"""A minimal hostile mongod for tests: speaks OP_MSG / OP_QUERY on 127.0.0.1.

``stall=True``: answers the handshake (hello / ping), then never answers a data
command (find, aggregate, count, listCollections, insert ...) — a server that
accepts and stalls. ``error_hosts``: the hello reply advertises a replica set
whose members are those internal names (for error-text tests).
"""

from __future__ import annotations

import datetime
import socket
import struct
import threading
from typing import Any

import bson

OP_REPLY = 1
OP_QUERY = 2004
OP_MSG = 2013
_HANDSHAKE = {"hello", "ismaster", "ping", "buildinfo", "endsessions", "saslstart"}


class FakeMongod:
    def __init__(self, *, stall: bool = False) -> None:
        self.stall = stall
        self.commands: list[str] = []
        self._release = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = int(self.sock.getsockname()[1])
        threading.Thread(target=self._accept, daemon=True).start()

    def _hello(self) -> dict[str, Any]:
        return {
            "ok": 1.0,
            "isWritablePrimary": True,
            "ismaster": True,
            "helloOk": True,
            "maxWireVersion": 21,
            "minWireVersion": 0,
            "maxBsonObjectSize": 16 * 1024 * 1024,
            "maxMessageSizeBytes": 48_000_000,
            "maxWriteBatchSize": 100_000,
            "localTime": datetime.datetime.now(datetime.UTC),
            "logicalSessionTimeoutMinutes": 30,
            "connectionId": 1,
        }

    def _answer(self, cmd: dict[str, Any]) -> dict[str, Any] | None:
        name = next(iter(cmd)).lower() if cmd else ""
        self.commands.append(name)
        if name in ("hello", "ismaster"):
            return self._hello()
        if name in _HANDSHAKE:
            return {"ok": 1.0}
        if self.stall:
            return None
        return {"ok": 1.0, "cursor": {"id": 0, "ns": "db.c", "firstBatch": []}, "n": 0}

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _recv(conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def _serve(self, conn: socket.socket) -> None:
        try:
            while True:
                length, req_id, _to, opcode = struct.unpack("<iiii", self._recv(conn, 16))
                body = self._recv(conn, length - 16)
                if opcode == OP_QUERY:
                    end = body.index(b"\x00", 4)
                    rest = body[end + 1 + 8 :]
                    cmd = bson.decode(rest[: struct.unpack("<i", rest[:4])[0]])
                    reply = self._answer(cmd)
                    if reply is None:
                        self._release.wait()
                        return
                    payload = struct.pack("<iqii", 0, 0, 0, 1) + bson.encode(reply)
                    header = struct.pack("<iiii", 16 + len(payload), 1, req_id, OP_REPLY)
                elif opcode == OP_MSG:
                    pos, cmd = 4, {}
                    while pos < len(body):
                        kind = body[pos]
                        size = struct.unpack("<i", body[pos + 1 : pos + 5])[0]
                        if kind == 0:
                            cmd = bson.decode(body[pos + 1 : pos + 1 + size])
                        pos += 1 + size
                    reply = self._answer(cmd)
                    if reply is None:
                        self._release.wait()
                        return
                    payload = struct.pack("<I", 0) + b"\x00" + bson.encode(reply)
                    header = struct.pack("<iiii", 16 + len(payload), 1, req_id, OP_MSG)
                else:
                    return
                conn.sendall(header + payload)
        except (ConnectionError, OSError, struct.error):
            return
        finally:
            conn.close()

    def close(self) -> None:
        self._release.set()
        self.sock.close()
