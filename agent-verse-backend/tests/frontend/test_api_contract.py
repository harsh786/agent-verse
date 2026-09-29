"""Frontend <-> backend route contract.

The frontend used to call ~30 routes that did not exist (GDPR privacy page,
connector detail, memory edit, skill edit/toggle/test, org artifacts, gateway
config ...). Most of them failed silently, because the callers swallowed the
404/405 into empty lists. This test scans the frontend source for literal API
paths passed to the HTTP helpers and fails when any has no matching
method + path in ``app.openapi()``.

How a call is recognised
------------------------
* ``request`` / ``apiFetch`` / ``fetch`` / ``apiClient`` / ``requestWithCancel``
  / ``uploadFile`` and ``<x>.get|post|put|patch|delete`` whose first argument is
  a string or template literal starting with ``/`` (after resolving
  ``${API_BASE}`` / ``${API_BASE_URL}`` and module-level string constants such
  as ``const V1 = '/api/v1'``);
* ``apiRequest('METHOD', '/path', ...)``;
* ``new EventSource('/path')`` (always GET).

The HTTP method comes from the helper name (``.post`` ...), the ``apiRequest``
argument, or a literal ``method: 'X'`` inside the call's arguments (GET
otherwise). ``${...}`` expressions become wildcard path segments, and a
trailing ``${qs}`` / ``?query`` is dropped. A path that is not literal (built
from a variable) cannot be checked and is ignored.

To accept a call that legitimately has no route, add it to ``EXCEPTIONS`` with
a comment saying why. Do not add entries to make the test pass for a broken
call: fix the call, add the backend route, or render an honest
"not available" state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from tests._paths import require_frontend

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# (METHOD, normalised frontend path) -> reason. Keep this short and commented.
EXCEPTIONS: dict[tuple[str, str], str] = {}

_PATH_CALLS = re.compile(
    r"""(?<![\w.$])(?P<name>
        request|apiFetch|fetch|apiClient|requestWithCancel|uploadFile
        |[A-Za-z_$][\w$]*\.(?P<verb>get|post|put|patch|delete)
    )\s*(?:<(?:[^<>()]|<[^<>()]*>)*>)?\s*\(\s*(?=[`'"])""",
    re.VERBOSE,
)
_API_REQUEST = re.compile(
    r"""(?<![\w.$])apiRequest\s*(?:<(?:[^<>()]|<[^<>()]*>)*>)?\s*\(\s*
        ['"](?P<method>GET|POST|PUT|PATCH|DELETE)['"]\s*,\s*(?=[`'"])""",
    re.VERBOSE,
)
_EVENT_SOURCE = re.compile(r"new\s+EventSource\s*\(\s*(?=[`'\"])")
_CONST = re.compile(r"""^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(['"`])(.*?)\2\s*;?\s*$""", re.M)
_METHOD_OPT = re.compile(r"""\bmethod\s*:\s*['"`](GET|POST|PUT|PATCH|DELETE)['"`]""", re.I)
_BASE_NAMES = {"API_BASE", "API_BASE_URL"}
# Objects whose .get/.post/... are HTTP calls (``params.get(...)`` is not).
_HTTP_OBJECTS = re.compile(r"^(api|apiClient|client|http|axios|[a-z]\w*Api|[a-z]\w*Client)$")


@dataclass(frozen=True)
class Call:
    file: str
    line: int
    method: str | None  # None = dynamic, match any method
    raw: str
    path: str  # normalised: ``*`` for a wildcard segment


def _read_literal(src: str, i: int) -> tuple[list[tuple[str, str]], int]:
    """Parse the string/template literal at ``src[i]``.

    Returns ``[(kind, text), ...]`` parts (kind ``lit`` or ``expr``) and the
    index just past the closing quote.
    """
    quote = src[i]
    i += 1
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    while i < len(src):
        c = src[i]
        if c == "\\":
            buf.append(src[i : i + 2])
            i += 2
            continue
        if c == quote:
            parts.append(("lit", "".join(buf)))
            return parts, i + 1
        if quote == "`" and src.startswith("${", i):
            parts.append(("lit", "".join(buf)))
            buf = []
            depth, j = 1, i + 2
            while j < len(src) and depth:
                if src[j] in "`'\"":
                    _, j = _read_literal(src, j)
                    continue
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                j += 1
            parts.append(("expr", src[i + 2 : j - 1].strip()))
            i = j
            continue
        buf.append(c)
        i += 1
    return parts, i


def _call_args_tail(src: str, i: int) -> str:
    """The rest of the call's argument list after index ``i`` (to the matching ``)``)."""
    depth, j = 1, i
    while j < len(src) and depth:
        c = src[j]
        if c in "`'\"":
            _, j = _read_literal(src, j)
            continue
        depth += {"(": 1, ")": -1}.get(c, 0)
        j += 1
    return src[i : j - 1]


def _resolve(parts: list[tuple[str, str]], consts: dict[str, str]) -> str | None:
    """Join literal parts into a normalised path, or None when not a literal API path."""
    out = ""
    for idx, (kind, text) in enumerate(parts):
        if kind == "lit":
            out += text
            continue
        if not out and text in _BASE_NAMES:
            continue  # `${API_BASE}/x` — the backend origin
        if not out and text in consts:
            out += consts[text]
            continue
        if not out:
            return None  # starts with a dynamic expression: not checkable
        rest = "".join(t for k, t in parts[idx + 1 :] if k == "lit")
        if not out.endswith("/") and not rest.startswith("/"):
            # `${x}` glued to the end of a literal segment (e.g. `/artifacts${qs}`):
            # a query-string suffix when nothing path-like follows.
            if not rest or rest.startswith("?"):
                break
        out += "\x00"  # wildcard placeholder
    out = out.split("?", 1)[0].split("#", 1)[0]
    if not out.startswith("/") or out.startswith("//"):
        return None
    segs = [s for s in out.split("/") if s]
    norm = ["*" if "\x00" in s else s for s in segs]
    return "/" + "/".join(norm)


def _strip_comments(src: str) -> str:
    """Blank out ``//`` and ``/* */`` comments (keeping newlines, so line numbers
    stay right) without touching string/template literals — JSDoc examples such
    as ``requestWithCancel<User[]>('/users')`` are not calls."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "`'\"":
            _, j = _read_literal(src, i)
            out.append(src[i:j])
            i = j
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _module_consts(src: str) -> dict[str, str]:
    consts: dict[str, str] = {}
    for name, _q, value in _CONST.findall(src):
        if "${" in value:
            value = re.sub(r"\$\{(\w+)\}", lambda m: consts.get(m.group(1), "\x01"), value)
            if "\x01" in value:
                continue
        if value.startswith("/"):
            consts[name] = value
    return consts


def _extract(src: str, file: str) -> list[Call]:
    src = _strip_comments(src)
    consts = _module_consts(src)
    calls: list[Call] = []

    def add(start: int, lit_at: int, method: str | None) -> None:
        parts, end = _read_literal(src, lit_at)
        path = _resolve(parts, consts)
        if path is None:
            return
        if method is None:
            m = _METHOD_OPT.search(_call_args_tail(src, end))
            method = m.group(1).upper() if m else "GET"
        raw = src[lit_at:end]
        calls.append(Call(file, src.count("\n", 0, start) + 1, method, raw, path))

    for m in _PATH_CALLS.finditer(src):
        name, verb = m.group("name"), m.group("verb")
        if verb:
            obj = name.rsplit(".", 1)[0]
            if not _HTTP_OBJECTS.match(obj):
                continue
            add(m.start(), m.end(), verb.upper())
        else:
            add(m.start(), m.end(), None)
    for m in _API_REQUEST.finditer(src):
        add(m.start(), m.end(), m.group("method").upper())
    for m in _EVENT_SOURCE.finditer(src):
        add(m.start(), m.end(), "GET")
    return calls


def _frontend_calls(src_root: Path) -> list[Call]:
    calls: list[Call] = []
    for path in sorted(src_root.rglob("*")):
        if path.suffix not in (".ts", ".tsx") or not path.is_file():
            continue
        rel = path.relative_to(src_root).as_posix()
        if (
            ".test." in path.name
            or ".spec." in path.name
            or "/__tests__/" in f"/{rel}"
            or "/mocks/" in f"/{rel}"
            or rel.startswith("test/")
        ):
            continue
        calls.extend(_extract(path.read_text(encoding="utf-8"), rel))
    return calls


@lru_cache(maxsize=1)
def _backend_routes() -> tuple[tuple[str, tuple[str, ...]], ...]:
    from app.main import create_app

    spec = create_app().openapi()
    routes = []
    for p, ops in spec["paths"].items():
        segs = tuple(s for s in p.split("/") if s)
        for m in ops:
            if m.upper() in _HTTP_METHODS:
                routes.append((m.upper(), segs))
    return tuple(routes)


def _seg_match(fe: str, be: str) -> bool:
    return fe == "*" or (be.startswith("{") and be.endswith("}")) or fe == be


def _matches(call: Call, routes: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[bool, bool]:
    """(path exists for some method, path exists for the call's method)."""
    segs = [s for s in call.path.split("/") if s]
    any_method = same_method = False
    for method, be in routes:
        if be and be[-1].endswith(":path}"):
            ok = len(segs) >= len(be) and all(
                _seg_match(f, b) for f, b in zip(segs, be[:-1], strict=False)
            )
        else:
            ok = len(segs) == len(be) and all(
                _seg_match(f, b) for f, b in zip(segs, be, strict=True)
            )
        if ok:
            any_method = True
            if call.method is None or call.method == method:
                same_method = True
    return any_method, same_method


# ── extractor self-tests (keep the scanner honest) ────────────────────────────


def test_extractor_recognises_the_call_shapes_the_frontend_uses() -> None:
    src = """
const V1 = '/api/v1';
request<Foo>(`/goals/${id}/cancel`, { method: "POST" });
apiFetch(`${API_BASE}/memory/${id}`, { method: 'PATCH', body: '{}' });
apiRequest<T>('DELETE', `/compliance/consent/${purpose}`);
apiClient.get<Bar>(`/v1/org/${orgId}/roles`);
request(`${V1}/workflows/${id}/publish`, { method: 'POST' });
fetch(`${API_BASE}/skills/${skill.id}/test`, { method: 'POST', headers: { a: 'b' } });
apiFetch(`/v1/org/${orgId}/artifacts${qs}`);
request(`/memory?${params.toString()}`);
new EventSource(`${API_BASE}/v1/org/${o}/graphify/${j}/stream?token=${t}`);
params.get('/not-an-api-call');
navigate('/goals');
request(path);
// request('/commented/out');
/** @example requestWithCancel<User[]>('/users') */
const url = 'http://x//y'; request('/after/string');
"""
    got = {(c.method, c.path) for c in _extract(src, "x.ts")}
    assert got == {
        ("POST", "/goals/*/cancel"),
        ("PATCH", "/memory/*"),
        ("DELETE", "/compliance/consent/*"),
        ("GET", "/v1/org/*/roles"),
        ("POST", "/api/v1/workflows/*/publish"),
        ("POST", "/skills/*/test"),
        ("GET", "/v1/org/*/artifacts"),
        ("GET", "/memory"),
        ("GET", "/v1/org/*/graphify/*/stream"),
        ("GET", "/after/string"),
    }


def test_matcher_uses_path_params_and_methods() -> None:
    routes = (("POST", ("schedules", "{schedule_id}", "pause")), ("GET", ("files", "{p:path}")))
    assert _matches(Call("f", 1, "POST", "", "/schedules/*/*"), routes) == (True, True)
    assert _matches(Call("f", 1, "GET", "", "/schedules/*/pause"), routes) == (True, False)
    assert _matches(Call("f", 1, "GET", "", "/schedules/*"), routes) == (False, False)
    assert _matches(Call("f", 1, "GET", "", "/files/a/b/c"), routes) == (True, True)


# ── the contract ──────────────────────────────────────────────────────────────


def test_every_literal_frontend_api_call_has_a_backend_route() -> None:
    src_root = require_frontend() / "src"
    calls = _frontend_calls(src_root)
    assert len(calls) > 300, f"scanner found only {len(calls)} calls — extractor broken?"
    routes = _backend_routes()

    missing: list[str] = []
    wrong_method: list[str] = []
    for call in calls:
        if (call.method or "*", call.path) in EXCEPTIONS:
            continue
        any_method, same_method = _matches(call, routes)
        where = f"{call.file}:{call.line} {call.method} {call.raw}"
        if not any_method:
            missing.append(where)
        elif not same_method:
            wrong_method.append(where)

    problems = [f"no such route: {m}" for m in missing] + [
        f"route exists, method does not: {m}" for m in wrong_method
    ]
    assert not problems, (
        f"{len(problems)} frontend API call(s) do not match app.openapi():\n  "
        + "\n  ".join(problems)
    )


def test_exceptions_are_still_needed() -> None:
    """An exception whose call was fixed (or whose route now exists) must go."""
    if not EXCEPTIONS:
        pytest.skip("no exceptions registered")
    src_root = require_frontend() / "src"
    seen = {(c.method or "*", c.path) for c in _frontend_calls(src_root)}
    routes = _backend_routes()
    stale = [
        key
        for key in EXCEPTIONS
        if key not in seen
        or _matches(Call("", 0, None if key[0] == "*" else key[0], "", key[1]), routes)[1]
    ]
    assert not stale, f"remove stale EXCEPTIONS entries: {stale}"
