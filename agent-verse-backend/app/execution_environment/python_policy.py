"""Deny-by-default AST policy for generated Python programs."""

from __future__ import annotations

import ast
from dataclasses import dataclass

ALLOWED_MODULES = frozenset(
    {
        "collections",
        "datetime",
        "decimal",
        "fractions",
        "functools",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
    }
)
DENIED_NAMES = frozenset(
    {
        "__import__",
        "asyncio",
        "breakpoint",
        "builtins",
        "compile",
        "ctypes",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "importlib",
        "input",
        "inspect",
        "locals",
        "marshal",
        "multiprocessing",
        "open",
        "os",
        "pathlib",
        "pickle",
        "resource",
        "setattr",
        "shutil",
        "signal",
        "socket",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "vars",
    }
)


@dataclass(frozen=True, slots=True)
class CodePolicyViolation:
    code: str


class PythonPolicyValidator:
    def __init__(self, *, maximum_ast_depth: int = 64) -> None:
        self._maximum_depth = maximum_ast_depth

    def validate(self, source: str) -> tuple[CodePolicyViolation, ...]:
        try:
            tree = ast.parse(source, mode="exec")
        except (SyntaxError, ValueError, RecursionError):
            return (CodePolicyViolation("syntax_invalid"),)
        violations: set[str] = set()

        def visit(node: ast.AST, depth: int) -> None:
            if depth > self._maximum_depth:
                violations.add("ast_depth_exceeded")
                return
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.level:
                    violations.add("relative_import_denied")
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if any(module not in ALLOWED_MODULES for module in modules):
                    violations.add("import_denied")
            if isinstance(node, ast.Name) and node.id in DENIED_NAMES:
                violations.add("name_denied")
            if isinstance(node, ast.Attribute) and (
                node.attr.startswith("__") or node.attr.endswith("__")
            ):
                violations.add("dunder_access_denied")
            for child in ast.iter_child_nodes(node):
                visit(child, depth + 1)

        visit(tree, 0)
        return tuple(CodePolicyViolation(code) for code in sorted(violations))


__all__ = ["ALLOWED_MODULES", "CodePolicyViolation", "PythonPolicyValidator"]
