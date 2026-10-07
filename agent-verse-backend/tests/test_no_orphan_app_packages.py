"""a10-F252-01: no top-level ``app`` package is dead code.

provenance, explainability_runtime, recovery, capabilities, plan_runtime,
sandbox_runtime, collaboration_runtime and ai_ops had zero importers outside
their own package (three were named only as documentary adapter strings of
PLANNED strategies) — scaffolds kept alive only by their own unit tests. They
were deleted; this guard keeps a new orphan from accumulating silently: every
top-level package under ``app/`` must be imported by code outside itself, or be
a declared entry point.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
# Packages whose importers are outside this repo by design.
_PUBLIC_LIBRARY_PACKAGES = {
    # AgentTestHarness: imported by the user's own test files that the
    # ``agentverse run-tests`` CLI command executes.
    "testing",
}


def _entry_point_packages() -> set[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    targets = data.get("project", {}).get("scripts", {}).values()
    return {t.split(":")[0].split(".")[1] for t in targets if t.startswith("app.")}


def _imported_top_level(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if len(parts) > 1 and parts[0] == "app":
                found.add(parts[1])
    return found


def test_every_top_level_app_package_has_an_importer_outside_itself() -> None:
    packages = {
        p.name for p in _APP.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    }
    importers: dict[str, set[str]] = {name: set() for name in packages}
    for path in _APP.rglob("*.py"):
        owner = path.relative_to(_APP).parts[0]
        for imported in _imported_top_level(path):
            if imported in importers and imported != owner:
                importers[imported].add(str(path.relative_to(_ROOT)))
    orphans = sorted(
        name for name, users in importers.items()
        if not users and name not in _entry_point_packages() | _PUBLIC_LIBRARY_PACKAGES
    )
    assert orphans == [], f"top-level app packages nothing imports: {orphans}"
