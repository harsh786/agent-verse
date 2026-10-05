"""A minimal fake ``mongod`` speaking OP_QUERY / OP_MSG, for hostile-server tests.

Modes:

* ``stall=True`` — answers ``hello`` / ``ping`` but never answers ``find`` /
  ``listCollections`` / ``aggregate`` / ``count`` (a server that accepts and then
  stalls).
* ``set_name`` + ``advertise="host:port"`` — a one-member replica set that, after
  the first explicit (non-handshake) ``hello``, i.e. AFTER the connector's member
  discovery, starts advertising an extra member in ``hello.hosts``.

Every command received is recorded (``commands``: lower-cased names;
``documents``: the command documents) so tests can assert on the query shape.
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

_STALL_COMMANDS = frozenset({"find", "listcollections", "aggregate", "count"})


class FakeMongod:
    def __init__(
        self,
        *,
        stall: bool = False,
        set_name: str | None = None,
        advertise: str | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.stall = stall
        self.set_name = set_name
        self.advertise = advertise
        self.host = host
        self.turned = False  # advertising the extra member yet
        self.commands: list[str] = []
        self.documents: list[dict[str, Any]] = []
        self._release = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, 0))
        self._sock.listen(64)
        self.port: int = self._sock.getsockname()[1]
        self._conns = 0
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def me(self) -> str:
        return f"{self.host}:{self.port}"

    def _hello(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
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
            "connectionId": self._conns,
        }
        if self.set_name:
            hosts = [self.me]
            if self.turned and self.advertise:
                hosts.append(self.advertise)
            doc.update(
                {
                    "setName": self.set_name,
                    "hosts": hosts,
                    "primary": self.me,
                    "me": self.me,
                    "setVersion": 1,
                    "electionId": bson.ObjectId("7fffffff0000000000000001"),
                }
            )
        return doc

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._conns += 1
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

    def _answer(self, cmd: dict[str, Any]) -> dict[str, Any] | None:
        name = next(iter(cmd)).lower() if cmd else ""
        self.commands.append(name)
        self.documents.append(dict(cmd))
        if name in ("hello", "ismaster"):
            reply = self._hello()
            if "client" not in cmd and self.set_name and self.advertise:
                # Discovery's explicit hello is answered clean; then the server turns.
                self.turned = True
            return reply
        if self.stall and name in _STALL_COMMANDS:
            return None
        if name == "listcollections":
            return {
                "ok": 1.0,
                "cursor": {
                    "id": 0,
                    "ns": "db.$cmd.listCollections",
                    "firstBatch": [{"name": "c", "type": "collection"}],
                },
            }
        if name == "find":
            return {"ok": 1.0, "cursor": {"id": 0, "ns": "db.c", "firstBatch": []}}
        return {"ok": 1.0}

    def _serve(self, conn: socket.socket) -> None:
        try:
            while True:
                header = self._recv(conn, 16)
                length, req_id, _resp_to, opcode = struct.unpack("<iiii", header)
                body = self._recv(conn, length - 16)
                if opcode == OP_QUERY:
                    end = body.index(b"\x00", 4)
                    rest = body[end + 1 + 8 :]
                    doc_len = struct.unpack("<i", rest[:4])[0]
                    reply = self._answer(bson.decode(rest[:doc_len]))
                    if reply is None:
                        self._release.wait()
                        return
                    payload = struct.pack("<iqii", 0, 0, 0, 1) + bson.encode(reply)
                    conn.sendall(
                        struct.pack("<iiii", 16 + len(payload), 1, req_id, OP_REPLY) + payload
                    )
                elif opcode == OP_MSG:
                    pos = 4
                    cmd: dict[str, Any] = {}
                    while pos < len(body):
                        kind = body[pos]
                        pos += 1
                        size = struct.unpack("<i", body[pos : pos + 4])[0]
                        if kind == 0:
                            cmd = bson.decode(body[pos : pos + size])
                        pos += size
                    reply = self._answer(cmd)
                    if reply is None:
                        self._release.wait()
                        return
                    payload = struct.pack("<I", 0) + b"\x00" + bson.encode(reply)
                    conn.sendall(
                        struct.pack("<iiii", 16 + len(payload), 1, req_id, OP_MSG) + payload
                    )
                else:
                    return
        except (ConnectionError, OSError):
            return
        finally:
            conn.close()

    def close(self) -> None:
        self._release.set()
        self._sock.close()


class Victim:
    """Records every TCP connection made to ``localhost:<port>`` (IPv4 and IPv6)."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        self._socks: list[socket.socket] = []
        s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s4.bind(("127.0.0.1", 0))
        s4.listen(16)
        self.port: int = s4.getsockname()[1]
        self._socks.append(s4)
        try:
            s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s6.bind(("::1", self.port))
            s6.listen(16)
            self._socks.append(s6)
        except OSError:
            pass
        for s in self._socks:
            threading.Thread(target=self._accept, args=(s,), daemon=True).start()

    def _accept(self, s: socket.socket) -> None:
        while True:
            try:
                conn, addr = s.accept()
            except OSError:
                return
            self.hits.append(str(addr))
            conn.close()

    def close(self) -> None:
        for s in self._socks:
            s.close()
