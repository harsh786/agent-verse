"""Guard: a function that SSRF-checks a URL must not then fetch it unpinned.

``assert_public_url`` / ``assert_public_url_async`` resolve the host and check
the answer; a plain ``httpx.AsyncClient`` then resolves it AGAIN to connect.
A DNS answer that flips between the two (rebinding) reaches 127.0.0.1 or the
cloud metadata service. Such call sites must build their client with
``app.net.ssrf_guard.public_async_client`` / ``public_client`` (connect-time
pinning) and follow redirects through ``request_public``.

``request_public`` re-checks every redirect hop, but on a plain client each hop
is still resolved twice, so it counts as a guard here too. The ingestion egress
wrappers (``assert_source_url``, ``source_url_is_allowed``, ``guarded_request``
in app/ingestion/connector_egress.py) are the same validate-then-connect
pattern; their callers use ``connector_egress.source_client``.

This test AST-scans every module under ``app/`` and fails when one function
both uses a guard (a call, or a reference such as
``asyncio.to_thread(assert_public_url, ...)``) and opens an unpinned httpx
connection (``httpx.AsyncClient(...)``, ``httpx.Client(...)``,
``httpx.stream/get/post/...``, attribute or imported-name form), unless the
site is listed in ``_ALLOWLIST`` with a reason. Stale allowlist entries also
fail, so the list can only shrink.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

_GUARD_NAMES = frozenset(
    {
        "assert_public_url",
        "assert_public_url_async",
        "request_public",
        # app/ingestion/connector_egress.py wrappers around assert_public_url
        "assert_source_url",
        "source_url_is_allowed",
        "guarded_request",
    }
)

# httpx entry points that connect using httpx's own DNS resolution.
_UNPINNED_HTTPX = frozenset(
    {"AsyncClient", "Client", "stream", "request", "get", "post", "put", "patch", "delete"}
)

# (path relative to app/, function qualname) -> reason. Keep this minimal.
_ALLOWLIST: dict[tuple[str, str], str] = {}


def _httpx_name_aliases(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "httpx":
            for alias in node.names:
                if alias.name in _UNPINNED_HTTPX:
                    names.add(alias.asname or alias.name)
    return names


def _httpx_module_aliases(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "httpx":
                    names.add(alias.asname or alias.name)
    return names or {"httpx"}


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """Nodes of *fn*'s own body — nested defs are scanned as their own sites."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _uses_guard(nodes: list[ast.AST]) -> bool:
    for node in nodes:
        if isinstance(node, ast.Name) and node.id in _GUARD_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _GUARD_NAMES:
            return True
    return False


def _violations_in(path: Path, root: Path = _APP) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_names = _httpx_name_aliases(tree)
    httpx_names = _httpx_module_aliases(tree)
    rel = path.relative_to(root).as_posix()
    found: list[tuple[str, str]] = []

    def is_unpinned(call: ast.Call) -> bool:
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _UNPINNED_HTTPX
            and isinstance(func.value, ast.Name)
            and func.value.id in httpx_names
        ):
            return True
        return isinstance(func, ast.Name) and func.id in imported_names

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                own = _own_nodes(child)
                unpinned = any(is_unpinned(n) for n in own if isinstance(n, ast.Call))
                if unpinned and _uses_guard(own):
                    found.append((rel, qual))
                visit(child, f"{qual}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _all_violations() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path in sorted(_APP.rglob("*.py")):
        if path.relative_to(_APP).as_posix() == "net/ssrf_guard.py":
            continue  # defines the guard and the pinned clients themselves
        out.update(_violations_in(path))
    return out


def test_no_ssrf_checked_url_is_fetched_with_an_unpinned_client() -> None:
    offenders = sorted(_all_violations() - set(_ALLOWLIST))
    assert not offenders, (
        "These functions SSRF-check a URL and then connect with a plain httpx "
        "client/helper (DNS-rebinding window). Use app.net.ssrf_guard."
        "public_async_client / public_client (+ request_public for redirects), or "
        "connector_egress.source_client in ingestion connectors:\n"
        + "\n".join(f"  {p}::{q}" for p, q in offenders)
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(set(_ALLOWLIST) - _all_violations())
    assert not stale, "Remove stale _ALLOWLIST entries (site migrated or renamed):\n" + "\n".join(
        f"  {p}::{q}" for p, q in stale
    )


def test_allowlist_entries_have_reasons() -> None:
    assert all(reason.strip() for reason in _ALLOWLIST.values())


def test_scanner_detects_every_unpinned_form(tmp_path: Path) -> None:
    """The scanner itself must flag attribute, imported-name and reference forms."""
    src = (
        "import httpx\n"
        "import httpx as hx\n"
        "from httpx import AsyncClient as AC\n"
        "from app.net.ssrf_guard import assert_public_url, assert_public_url_async\n"
        "async def a(u):\n"
        "    assert_public_url(u)\n"
        "    async with httpx.AsyncClient() as c:\n"
        "        await c.get(u)\n"
        "class K:\n"
        "    async def b(self, u):\n"
        "        await assert_public_url_async(u)\n"
        "        async with AC() as c:\n"
        "            await c.get(u)\n"
        "async def c(u):\n"
        "    await asyncio.to_thread(assert_public_url, u)\n"
        "    async with httpx.AsyncClient() as cl:\n"
        "        await cl.get(u)\n"
        "def d(u):\n"
        "    assert_public_url(u)\n"
        "    with hx.stream('GET', u) as r:\n"
        "        r.read()\n"
        "async def e(u):\n"
        "    async with httpx.AsyncClient() as cl:\n"
        "        await request_public(cl, 'GET', u)\n"
        "async def ok(u):\n"
        "    assert_public_url(u)\n"
        "    async with public_async_client() as c:\n"
        "        await request_public(c, 'GET', u)\n"
        "async def unguarded(u):\n"
        "    async with httpx.AsyncClient() as c:\n"
        "        await c.get('https://api.example.com')\n"
        "async def outer(u):\n"
        "    async def inner():\n"
        "        assert_public_url(u)\n"
        "        async with httpx.AsyncClient() as c:\n"
        "            await c.get(u)\n"
        "    await inner()\n"
    )
    target = tmp_path / "m.py"
    target.write_text(src)
    assert sorted(_violations_in(target, root=tmp_path)) == [
        ("m.py", "K.b"),
        ("m.py", "a"),
        ("m.py", "c"),
        ("m.py", "d"),
        ("m.py", "e"),
        ("m.py", "outer.inner"),
    ]


# ── WebSockets (SSRF-05) ──────────────────────────────────────────────────────
# ``websockets.connect`` resolves the host itself (and honours env proxies), so
# a URL checked beforehand can rebind to an internal address at connect time.
# Outbound WebSockets go through app.net.ssrf_guard.connect_public_websocket.


def _ws_connect_sites(path: Path, root: Path = _APP) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(root).as_posix()
    module_aliases: set[str] = set()
    connect_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "websockets":
                    module_aliases.add(alias.asname or "websockets")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("websockets"):
            for alias in node.names:
                if alias.name == "connect":
                    connect_names.add(alias.asname or alias.name)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "connect":
            base = node.value
            if (isinstance(base, ast.Name) and base.id in module_aliases) or (
                isinstance(base, ast.Attribute) and base.attr == "client"
            ):
                found.append((rel, node.lineno))
        elif isinstance(node, ast.Name) and node.id in connect_names:
            found.append((rel, node.lineno))
    return sorted(found)


def test_no_raw_websockets_connect_outside_the_guard() -> None:
    offenders: list[tuple[str, int]] = []
    for path in sorted(_APP.rglob("*.py")):
        if path.relative_to(_APP).as_posix() == "net/ssrf_guard.py":
            continue
        offenders.extend(_ws_connect_sites(path))
    assert not offenders, (
        "Raw websockets.connect re-resolves DNS at connect time (rebinding). Use "
        "app.net.ssrf_guard.connect_public_websocket:\n"
        + "\n".join(f"  {p}:{line}" for p, line in offenders)
    )


def test_ws_scanner_detects_every_form(tmp_path: Path) -> None:
    src = (
        "import websockets\n"
        "import websockets.asyncio.client as wac\n"
        "from websockets.asyncio.client import connect as wsc\n"
        "async def a(u):\n"
        "    await websockets.connect(u)\n"
        "async def b(u):\n"
        "    await wac.connect(u)\n"
        "async def c(u):\n"
        "    await wsc(u)\n"
        "async def d(u):\n"
        "    await websockets.asyncio.client.connect(u)\n"
        "async def ok(db):\n"
        "    await db.connect()\n"
    )
    target = tmp_path / "m.py"
    target.write_text(src)
    assert [line for _p, line in _ws_connect_sites(target, root=tmp_path)] == [5, 7, 9, 11]
