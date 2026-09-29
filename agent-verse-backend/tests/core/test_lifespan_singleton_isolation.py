"""Lifespan-wired process-global singletons are restored between tests.

Regression: a lifespan test built with a fake ``SimpleNamespace`` session
factory left it bound to module singletons (template store, MFA store, …), so
later tests in the same session failed with ``no attribute 'scalars'`` / 503s.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from tests import _lifespan_singletons as singletons

_MAIN = Path(__file__).resolve().parents[2] / "app" / "main.py"

# Accessor functions main.py calls to reach a singleton -> what they return/replace.
_ACCESSORS = {
    "get_twin": ("app.org.digital_twin", "_twin"),
    "get_dept_memory": ("app.memory.dept_memory", "_dept_memory"),
    "get_reflexion_wirer": ("app.agent.reflexion_wirer", "_default_reflexion_wirer"),
    "get_llm_config_store": ("app.services.llm_config_store", "_llm_config_store"),
}
_WIRING = re.compile(
    r"(?P<target>[A-Za-z_]\w*)(?P<call>\(\))?(?:\.\w+)*"
    r"\.(?:set_db|set_db_factory|set_session_factory)\(\s*\w*db_factory\s*\)"
    r"|(?P<target2>[A-Za-z_]\w*)(?:\.\w+)*\._db_factory\s*=\s*\w*db_factory"
    r"|(?P<accessor>[A-Za-z_]\w*)\(\s*db_factory\s*=\s*\w*db_factory\s*\)"
)
_IMPORT = re.compile(r"from (app[\w.]*) import (\w+)(?: as (\w+))?\s*$")


def _module_level_wiring() -> set[tuple[str, str]]:
    """(module, attr) of every module-level singleton main.py binds a db_factory to."""
    lines = _MAIN.read_text().splitlines()
    found: set[tuple[str, str]] = set()
    for i, line in enumerate(lines):
        m = _WIRING.search(line)
        if not m:
            continue
        name = m.group("target") or m.group("target2") or m.group("accessor")
        if name in _ACCESSORS:
            found.add(_ACCESSORS[name])
            continue
        # A local variable assigned from an accessor call (x = get_foo() or …).
        for j in range(i, max(-1, i - 60), -1):
            assign = re.match(rf"\s*{re.escape(name)}\s*=\s*(.+)$", lines[j])
            if assign:
                hit = next((a for a in _ACCESSORS if f"{a}(" in assign.group(1)), None)
                if hit:
                    found.add(_ACCESSORS[hit])
                    name = ""
                break
        if not name:
            continue
        # Resolve a local alias to the `from app.x import y [as alias]` above it.
        for j in range(i, max(-1, i - 60), -1):
            im = _IMPORT.search(lines[j])
            if im and (im.group(3) or im.group(2)) == name:
                module, attr = im.group(1), im.group(2)
                if attr in _ACCESSORS:
                    found.add(_ACCESSORS[attr])
                elif not callable(getattr(importlib.import_module(module), attr, None)) or not (
                    attr[:1].isupper()
                ):
                    found.add((module, attr))
                break
    return found


def test_every_module_singleton_wired_by_the_lifespan_is_restored() -> None:
    covered = set(singletons.WIRED_OBJECTS) | set(singletons.REPLACED_GLOBALS)
    wired = _module_level_wiring()
    assert wired, "found no lifespan wiring - the scan is broken"
    missing = sorted(wired - covered)
    assert not missing, (
        "app/main.py binds db_factory to these process-global singletons, but "
        f"tests/_lifespan_singletons.py does not restore them: {missing}"
    )


def test_snapshot_restore_undoes_a_fake_lifespan_binding() -> None:
    for module_name, _ in (*singletons.WIRED_OBJECTS, *singletons.REPLACED_GLOBALS):
        importlib.import_module(module_name)
    saved = singletons.snapshot()
    fake = object()
    for module_name, attr in singletons.WIRED_OBJECTS:
        obj = getattr(importlib.import_module(module_name), attr)
        for name in singletons.DB_ATTRS:
            if hasattr(obj, name):
                setattr(obj, name, fake)
    for module_name, attr in singletons.REPLACED_GLOBALS:
        setattr(importlib.import_module(module_name), attr, fake)

    singletons.restore(saved)

    leaked = []
    for module_name, attr in singletons.WIRED_OBJECTS:
        obj = getattr(importlib.import_module(module_name), attr)
        leaked += [f"{module_name}.{attr}.{n}" for n in singletons.DB_ATTRS
                   if getattr(obj, n, None) is fake]
    leaked += [f"{m}.{a}" for m, a in singletons.REPLACED_GLOBALS
               if getattr(importlib.import_module(m), a) is fake]
    assert not leaked, leaked
