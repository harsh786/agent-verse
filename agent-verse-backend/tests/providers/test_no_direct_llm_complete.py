"""Guard: no new direct ``provider.complete(...)`` calls under ``app/``.

A direct call skips everything :func:`app.providers.guarded_completion.complete_decision`
(and the agent roles' ``complete_with_failover``) provide: the goal / tenant
budget, the token ledger, the bounded timeout and the per-model circuit breaker.
Many call sites did exactly that; they were migrated, and this scan keeps new
ones from creeping back.

Every remaining ``.complete(`` call outside ``app/providers/`` must be listed in
:data:`ALLOWED` under its ``"<path>::<enclosing qualname>"`` with the number of
calls and a reason. The test also fails on a stale entry (count changed or call
gone), so the list shrinks as debt is paid down. To fix a failure: route the
call through ``complete_decision(provider, request, role=..., tenant_ctx=... |
tenant_id=...)`` — do not add it here unless the receiver is not an LLM provider
or the call is itself a guarded wrapper.
"""

from __future__ import annotations

import ast
import collections
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"

_TRACING = (
    "transparent tracing proxy: adds spans only; every caller reaches it through "
    "complete_decision / complete_with_failover, which apply budget, breaker and timeout"
)
_NOT_LLM = "receiver is not an LLM provider"
_OWNED = "pending migration in this wave (PROV-25)"

# "<path relative to app/>::<qualname>": (number of .complete( calls, reason)
ALLOWED: dict[str, tuple[int, str]] = {
    # ── not a direct LLM spend path ───────────────────────────────────────────
    "observability/traced_provider.py::TracedProvider.complete": (1, _TRACING),
    "api/goals.py::submit_goal": (1, _NOT_LLM + " (idempotency record)"),
    "scaling/memory_tasks.py::process_due_memories": (1, _NOT_LLM + " (prospective memory)"),
    # ── pending, owned by later items of this wave ────────────────────────────
    "ai_router/shadow_router.py::ShadowRouter.shadow_call": (3, _OWNED),
}


def _scan() -> dict[str, int]:
    found: collections.Counter[str] = collections.Counter()
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        if rel.startswith("providers/"):
            continue  # the providers themselves and guarded_completion
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def walk(node: ast.AST, stack: list[str], rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    walk(child, [*stack, child.name])
                    continue
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "complete"
                ):
                    found[f"{rel}::{'.'.join(stack) or '<module>'}"] += 1
                walk(child, stack)

        walk(tree, [])
    return dict(found)


def test_no_unlisted_direct_llm_complete_calls() -> None:
    found = _scan()
    unlisted = sorted(
        f"{key} ({count} call(s))"
        for key, count in found.items()
        if key not in ALLOWED or ALLOWED[key][0] < count
    )
    assert not unlisted, (
        "Direct provider.complete() call(s) bypass budget, ledger, timeout and circuit "
        "breaker — route them through app.providers.guarded_completion.complete_decision:\n  "
        + "\n  ".join(unlisted)
    )


def test_allowlist_has_no_stale_entries() -> None:
    found = _scan()
    stale = sorted(
        f"{key} (listed {count}, found {found.get(key, 0)})"
        for key, (count, _reason) in ALLOWED.items()
        if found.get(key, 0) != count
    )
    assert not stale, "Update ALLOWED — these entries no longer match:\n  " + "\n  ".join(stale)


def test_every_allowlist_entry_has_a_reason() -> None:
    assert all(reason.strip() for _count, reason in ALLOWED.values())


# ── streaming calls (PROV-02) ──────────────────────────────────────────────────
# ``stream_complete`` / ``stream_tokens`` cannot go through complete_decision, so
# every streaming call site (incl. ``getattr(p, "stream_complete")``) must be
# listed with how it is metered: budget preflight + post-stream charge.
_STREAMS = ("stream_complete", "stream_tokens")

STREAM_ALLOWED: dict[str, tuple[int, str]] = {
    "chat/service.py::ChatService.run_qa": (
        1,
        "preflight_decision before the stream, charge_streamed after it (PROV-02)",
    ),
    "agent/graph.py::AgentGraph._stream_with_failover": (
        1,
        "executor step: per-call timeout here, charged by the executor cost path "
        "(budget gate + ledger in executor_mixin)",
    ),
    "observability/traced_provider.py::TracedProvider.stream_tokens": (
        1,
        _TRACING,
    ),
}


def _scan_streams() -> dict[str, int]:
    found: collections.Counter[str] = collections.Counter()
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        if rel.startswith("providers/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def walk(node: ast.AST, stack: list[str], rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    walk(child, [*stack, child.name])
                    continue
                if isinstance(child, ast.Call):
                    hit = isinstance(child.func, ast.Attribute) and child.func.attr in _STREAMS
                    hit = hit or (
                        isinstance(child.func, ast.Name)
                        and child.func.id == "getattr"
                        and len(child.args) >= 2
                        and isinstance(child.args[1], ast.Constant)
                        and child.args[1].value in _STREAMS
                    )
                    if hit:
                        found[f"{rel}::{'.'.join(stack) or '<module>'}"] += 1
                walk(child, stack)

        walk(tree, [])
    return dict(found)


def test_no_unlisted_streaming_llm_calls() -> None:
    found = _scan_streams()
    unlisted = sorted(
        f"{k} ({n})" for k, n in found.items()
        if k not in STREAM_ALLOWED or STREAM_ALLOWED[k][0] < n
    )
    assert not unlisted, (
        "Streaming LLM call(s) without budget preflight/charge — meter them like "
        "ChatService.run_qa (preflight_decision + charge_streamed):\n  " + "\n  ".join(unlisted)
    )


def test_stream_allowlist_has_no_stale_entries() -> None:
    found = _scan_streams()
    stale = sorted(
        f"{k} (listed {n}, found {found.get(k, 0)})"
        for k, (n, _r) in STREAM_ALLOWED.items()
        if found.get(k, 0) != n
    )
    assert not stale, "Update STREAM_ALLOWED:\n  " + "\n  ".join(stale)
