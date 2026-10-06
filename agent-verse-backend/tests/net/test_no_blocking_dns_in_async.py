"""SSRF-04: async code must not run the SSRF guard's blocking DNS on the event loop.

``assert_public_url`` resolves the host with a synchronous ``getaddrinfo``.
Called directly from an ``async def`` it blocks the whole event loop for the
DNS round trip (seconds on a slow or hostile resolver — every request on the
replica stalls). Async code uses ``assert_public_url_async`` (or wraps the
check in ``asyncio.to_thread``).

This AST scan fails on a direct synchronous guard call inside an ``async def``
under ``app/``. Sites owned by other packages that still do it are listed in
``_KNOWN`` (each must still exist — the list can only shrink).
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

_SYNC_GUARDS = frozenset(
    {
        "assert_public_url",
        "is_public_url",
        "is_ssrf_blocked",
        "resolve_and_check_host",
        "_assert_egress_allowed",  # app/mcp/client.py wrapper
    }
)

# (path relative to app/, async function qualname) -> owner / reason.
_OTHER_OWNER = "owned by another fix package — migrate to assert_public_url_async"
_KNOWN: dict[tuple[str, str], str] = {
    ("agent/tools/a2a_call.py", "call_external_a2a_agent"): _OTHER_OWNER,  # agent-core
    ("gateway/router.py", "_download_command_file"): _OTHER_OWNER,  # triggers
    ("rag_platform/hosted_reranker.py", "HostedReranker.rerank"): _OTHER_OWNER,  # knowledge
}


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _sites(path: Path, root: Path = _APP) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(root).as_posix()
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{child.name}"
                if isinstance(child, ast.AsyncFunctionDef):
                    for n in _own_nodes(child):
                        if not isinstance(n, ast.Call):
                            continue
                        f = n.func
                        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                        if name in _SYNC_GUARDS:
                            found.append((rel, qual))
                            break
                visit(child, f"{qual}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _all_sites() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path in sorted(_APP.rglob("*.py")):
        if path.relative_to(_APP).as_posix() == "net/ssrf_guard.py":
            continue
        out.update(_sites(path))
    return out


def test_no_blocking_ssrf_dns_on_the_event_loop() -> None:
    offenders = sorted(_all_sites() - set(_KNOWN))
    assert not offenders, (
        "These async functions call the synchronous SSRF guard (blocking DNS on the "
        "event loop). Use app.net.ssrf_guard.assert_public_url_async:\n"
        + "\n".join(f"  {p}::{q}" for p, q in offenders)
    )


def test_known_list_has_no_stale_entries() -> None:
    stale = sorted(set(_KNOWN) - _all_sites())
    assert not stale, "Remove fixed entries from _KNOWN:\n" + "\n".join(
        f"  {p}::{q}" for p, q in stale
    )


def test_scanner_flags_direct_calls_only(tmp_path: Path) -> None:
    src = (
        "import asyncio\n"
        "from app.net import ssrf_guard\n"
        "from app.net.ssrf_guard import assert_public_url, assert_public_url_async\n"
        "async def bad(u):\n"
        "    assert_public_url(u)\n"
        "async def bad_attr(u):\n"
        "    ssrf_guard.assert_public_url(u)\n"
        "async def ok_async(u):\n"
        "    await assert_public_url_async(u)\n"
        "async def ok_thread(u):\n"
        "    await asyncio.to_thread(assert_public_url, u)\n"
        "def ok_sync(u):\n"
        "    assert_public_url(u)\n"
        "class K:\n"
        "    async def bad_method(self, u):\n"
        "        assert_public_url(u)\n"
    )
    target = tmp_path / "m.py"
    target.write_text(src)
    assert sorted(_sites(target, root=tmp_path)) == [
        ("m.py", "K.bad_method"),
        ("m.py", "bad"),
        ("m.py", "bad_attr"),
    ]
