"""Guard: code that owns a DB session and touches a FORCE-RLS table must scope it.

This bug class has recurred across the codebase more than any other, and it is
invisible in normal development because a superuser dev connection ignores row
level security entirely. Under the least-privilege role the app actually runs
as, a query with no `app.tenant_id` GUC silently matches zero rows (reads) or is
rejected outright (writes) — and in every instance found so far the failure was
swallowed and followed by a log line claiming success:

    cost_ledger_write_failed  new row violates row-level security policy
    llm_cost_recorded         cost_usd=0.00075 ...

Instances fixed so far: compliance_requests, decision_traces, tool_capabilities,
GDPR erasure, eval scorecards, app/api/replay.py, the knowledge graph, the audit
trail (`AuditFlusher`), legal holds, and the cost ledger.

Rather than rediscover this one module at a time, this test fails whenever a
module that opens its OWN session references a FORCE-RLS table without
establishing an RLS context. Modules that receive a session from a caller are
out of scope — the caller owns the context — and are listed in
`_CALLER_SCOPED`.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
MIGRATIONS = APP / "db" / "migrations"

# Opens its own session (any of these), so it must set the context itself.
_OWNS_SESSION = re.compile(
    r"async with\s+(?:\(\s*)?(?:self\._db|self\._db_factory|self\._session_factory|db|_db)\(\)"
)
_RLS = ("sqlalchemy_rls_context", "rls_context", "system_session", "app.tenant_id")
_SQL_REF = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN|DELETE\s+FROM)\s+([a-z_][a-z0-9_]*)", re.I
)

# Modules that take a session from their caller, who owns the RLS scope.
_CALLER_SCOPED = {
    # app/rag/gateway.py wraps these in sqlalchemy_rls_context before calling;
    # the module docstring states it reads "in an existing RLS scope".
    "rag/agentic/patterns/graph.py",
    "org/brain_store.py",
    "gateway/conversation.py",
}

# Known backlog — NOT approved, NOT safe. Each of these opens its own session
# and queries a FORCE-RLS table with no RLS context, exactly like the audit
# trail, legal holds and cost ledger did before they were fixed. They are listed
# here so this guard can ratchet (no NEW instances) while the existing ones are
# burned down; remediating them is per-site work, because `tenant_id` is not in
# scope at every session site and a blanket edit would raise NameError.
#
# Remove an entry as it is fixed. Do not add to this set.
_KNOWN_BACKLOG: set[str] = set()
# Empty, and it must stay that way. Every module that once appeared here has been
# scoped; `test_the_rls_backlog_only_shrinks` fails if an entry is added back and
# then fixed without being removed, and the test above fails outright on any NEW
# module that queries a FORCE-RLS table from a session it opened itself.
#
# One site is deliberately excluded rather than listed here: the cross-tenant
# "platform average" aggregate in app/api/enterprise.py (get_benchmarks). It is
# inert under RLS today, and switching it on would newly expose aggregate
# statistics over other tenants' data — a privacy decision, not a bug fix. It is
# documented in place.


def _force_rls_tables() -> set[str]:
    tables: set[str] = set()
    pattern = re.compile(r"ALTER TABLE (\w+) FORCE ROW LEVEL SECURITY")
    loop_pattern = re.compile(r"ALTER TABLE \{table\} FORCE ROW LEVEL SECURITY")
    for path in (MIGRATIONS / "versions").glob("*.py"):
        text = path.read_text(errors="ignore")
        tables.update(pattern.findall(text))
        if loop_pattern.search(text):
            # f-string loop: the table names come from a tuple in the same file.
            for block in re.findall(r"_TABLES\s*=\s*\(([^)]*)\)", text) or re.findall(
                r'for table in \(([^)]*)\)', text
            ):
                tables.update(re.findall(r'"(\w+)"', block))
    return tables


def test_no_module_queries_a_force_rls_table_without_scoping_its_session() -> None:
    tables = _force_rls_tables()
    assert len(tables) > 50, f"table discovery broke, only found {len(tables)}"

    offenders: dict[str, list[str]] = {}
    for path in APP.rglob("*.py"):
        if MIGRATIONS in path.parents:
            continue
        text = path.read_text(errors="ignore")
        rel = str(path.relative_to(APP))
        if rel in _CALLER_SCOPED or rel in _KNOWN_BACKLOG:
            continue
        if not _OWNS_SESSION.search(text):
            continue
        if any(marker in text for marker in _RLS):
            continue
        refs = sorted({m.group(1).lower() for m in _SQL_REF.finditer(text)} & tables)
        if refs:
            offenders[rel] = refs

    assert not offenders, (
        "These modules open their own DB session and query a FORCE ROW LEVEL "
        "SECURITY table without establishing an RLS context. Under the app's "
        "real least-privilege role those statements match zero rows or are "
        "rejected, and the failure is usually swallowed:\n"
        + "\n".join(f"  {mod}: {', '.join(t)}" for mod, t in sorted(offenders.items()))
    )


def test_the_rls_backlog_only_shrinks() -> None:
    """Every module in _KNOWN_BACKLOG must still exist and still be an offender.

    If one has been fixed, this fails so the entry is removed rather than left
    to rot — the backlog is a ratchet, not a permanent exemption list.
    """
    tables = _force_rls_tables()
    stale: list[str] = []
    for rel in sorted(_KNOWN_BACKLOG):
        path = APP / rel
        if not path.exists():
            stale.append(f"{rel} (file gone)")
            continue
        text = path.read_text(errors="ignore")
        still_offending = (
            _OWNS_SESSION.search(text)
            and not any(marker in text for marker in _RLS)
            and ({m.group(1).lower() for m in _SQL_REF.finditer(text)} & tables)
        )
        if not still_offending:
            stale.append(f"{rel} (fixed — remove it from _KNOWN_BACKLOG)")
    assert not stale, "Stale _KNOWN_BACKLOG entries:\n  " + "\n  ".join(stale)
