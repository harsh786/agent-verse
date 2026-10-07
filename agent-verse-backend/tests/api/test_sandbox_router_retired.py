"""a10-F241-01/02: the duplicate /sandbox surface is retired.

``POST /sandbox/goals`` ran the same :class:`SimulationRunner` as
``POST /enterprise/simulation`` (the surface the frontend, Agent Lab and
playground use), had no in-repo consumer, carried a dead dry-run fallback (the
runner is always wired) and served a static ``/sandbox/config`` that claimed
"Cost is simulated" / "deterministic mock data" even when a real LLM planned.
One mock-tool simulation surface remains.
"""

from __future__ import annotations

from app.main import create_app


def _paths() -> set[str]:
    return set(create_app().openapi().get("paths", {}))


def test_sandbox_routes_are_not_mounted() -> None:
    paths = _paths()
    assert not any(p == "/sandbox" or p.startswith("/sandbox/") for p in paths)


def test_the_canonical_simulation_surface_remains() -> None:
    paths = _paths()
    assert "/enterprise/simulation" in paths
    assert "/enterprise/simulation/stream" in paths
    assert "/enterprise/simulation/{run_id}" in paths
