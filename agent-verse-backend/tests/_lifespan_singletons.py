"""Process-global singletons the app lifespan wires to a DB session factory.

``create_app`` / its lifespan binds ``db_factory`` onto module-level objects
(``obj.set_db(db_factory)``, ``obj._db_factory = db_factory``) and, for the
reflexion wirer, replaces the module global itself. A test that runs the
lifespan with a fake factory (e.g. a ``SimpleNamespace`` session) used to leave
those bound for the rest of the session, so unrelated later tests hit the fake
DB — ``'SimpleNamespace' object has no attribute 'scalars'`` in the template
store, 503s from MFA — only in suite order.

``snapshot()`` / ``restore()`` put them back; tests/conftest.py applies them
around every test. ``tests/core/test_lifespan_singleton_isolation.py`` fails if
app/main.py starts wiring a module-level singleton this list does not cover.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

# (module, attribute): an object whose DB attributes the lifespan sets.
WIRED_OBJECTS: tuple[tuple[str, str], ...] = (
    ("app.api.mfa", "_mfa_db_store"),
    ("app.api.templates", "template_store"),
    ("app.chat.router", "_services_api"),
    ("app.auth.agent_credentials", "_agent_credential_store"),
    ("app.knowledge_graph.store", "kg_store"),
    ("app.intelligence.prompt_optimizer", "_default_optimizer"),
    ("app.org.digital_twin", "_twin"),
    ("app.memory.dept_memory", "_dept_memory"),
)
# (module, attribute): a module global the lifespan REPLACES (restored by identity).
REPLACED_GLOBALS: tuple[tuple[str, str], ...] = (
    ("app.agent.reflexion_wirer", "_default_reflexion_wirer"),
    ("app.services.llm_config_store", "_llm_config_store"),
)
DB_ATTRS: tuple[str, ...] = ("_db", "_db_factory", "_session_factory")

Snapshot = tuple[list[tuple[object, str, object]], list[tuple[Any, str, object]], set[str]]


def snapshot() -> Snapshot:
    """Record the current DB attributes / globals of every imported singleton."""
    attrs: list[tuple[object, str, object]] = []
    for module_name, attr in WIRED_OBJECTS:
        module = sys.modules.get(module_name)
        obj = getattr(module, attr, None) if module is not None else None
        if obj is None:
            continue
        for name in DB_ATTRS:
            if hasattr(obj, name):
                attrs.append((obj, name, getattr(obj, name)))
    globals_: list[tuple[Any, str, object]] = []
    for module_name, attr in REPLACED_GLOBALS:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attr):
            globals_.append((module, attr, getattr(module, attr)))
    imported = {m for m, _ in (*WIRED_OBJECTS, *REPLACED_GLOBALS) if m in sys.modules}
    return attrs, globals_, imported


def restore(saved: Snapshot) -> None:
    """Undo every lifespan binding made since ``saved`` was taken."""
    attrs, globals_, imported_before = saved
    for obj, name, value in attrs:
        with contextlib.suppress(Exception):
            setattr(obj, name, value)
    for module, attr, value in globals_:
        with contextlib.suppress(Exception):
            setattr(module, attr, value)
    # Modules first imported during the test: start them unwired next time.
    for module_name, attr in WIRED_OBJECTS:
        if module_name in imported_before:
            continue
        module = sys.modules.get(module_name)
        obj = getattr(module, attr, None) if module is not None else None
        if obj is None:
            continue
        for name in DB_ATTRS:
            if hasattr(obj, name):
                with contextlib.suppress(Exception):
                    setattr(obj, name, None)
    for module_name, attr in REPLACED_GLOBALS:
        if module_name in imported_before:
            continue
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attr):
            with contextlib.suppress(Exception):
                setattr(module, attr, None)
