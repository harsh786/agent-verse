"""ExpressionEngine — safe expression evaluator for conditional branches.

Uses `simpleeval` to evaluate expressions without allowing dangerous
Python builtins (no import, no exec, no open, no os, no sys).

Supports:
  - Arithmetic:    +, -, *, /, %, //
  - Comparison:    ==, !=, >, <, >=, <=
  - Logical:       and, or, not
  - Membership:    in, not in
  - Ternary:       X if COND else Y
  - Functions:     len(), str(), int(), float(), bool(),
                   lower(), upper(), contains(), startswith(), endswith(),
                   round(), abs(), min(), max()

All {{...}} expressions in condition strings are resolved by ContextResolver
BEFORE being passed to this engine.
"""
from __future__ import annotations

import re

from app.observability.logging import get_logger

_log = get_logger(__name__)


class ExpressionSecurityError(ValueError):
    """Raised when a blocked construct is detected."""


class ExpressionEvalError(ValueError):
    """Raised when expression evaluation fails."""


# Safe functions exposed to expressions
_SAFE_FUNCTIONS = {
    "len":        len,
    "str":        str,
    "int":        int,
    "float":      float,
    "bool":       bool,
    "lower":      lambda s: str(s).lower(),
    "upper":      lambda s: str(s).upper(),
    "contains":   lambda s, sub: sub in str(s),
    "startswith": lambda s, pre: str(s).startswith(str(pre)),
    "endswith":   lambda s, suf: str(s).endswith(str(suf)),
    "round":      round,
    "abs":        abs,
    "min":        min,
    "max":        max,
}

# Blocked keywords that indicate dangerous patterns
_BLOCKED_PATTERNS = re.compile(
    r"\b(import|__import__|exec|eval|compile|open|os|sys|subprocess|"
    r"builtins|globals|locals|getattr|setattr|delattr|vars|dir)\b"
)


class ExpressionEngine:
    """Evaluates conditional branch expressions safely."""

    def evaluate(self, expression: str) -> bool:
        """Evaluate a boolean expression. Returns True/False."""
        # Security check before evaluation
        if _BLOCKED_PATTERNS.search(expression):
            raise ExpressionSecurityError(
                f"Blocked construct in expression: {expression!r}"
            )

        try:
            # Try simpleeval first (preferred — has strict function allowlist)
            return self._eval_simpleeval(expression)
        except ImportError:
            # Fallback: ast.literal_eval for simple cases
            return self._eval_fallback(expression)

    def _eval_simpleeval(self, expression: str) -> bool:
        try:
            import simpleeval
        except ImportError as exc:
            raise ImportError("simpleeval not installed") from exc

        try:
            # Use EvalWithCompoundTypes for list/dict literals in expressions
            s = simpleeval.EvalWithCompoundTypes(functions=_SAFE_FUNCTIONS)
            result = s.eval(expression)
            return bool(result)
        except simpleeval.FeatureNotAvailable as exc:
            raise ExpressionSecurityError(
                f"Unsafe expression feature: {exc}"
            ) from exc
        except Exception as exc:
            raise ExpressionEvalError(
                f"Expression error in {expression!r}: {exc}"
            ) from exc

    def _eval_fallback(self, expression: str) -> bool:
        """Minimal fallback for environments without simpleeval."""
        # Only allow simple comparisons: "VALUE OP VALUE"
        # This covers the most common workflow conditional patterns
        try:
            # Allow: x > y, x < y, x >= y, x <= y, x == y, x != y
            # and simple: true, false, not expr
            import ast
            tree = ast.parse(expression, mode="eval")
            # Walk and reject anything that isn't safe
            for node in ast.walk(tree):
                if isinstance(node, (
                    ast.Import, ast.ImportFrom, ast.Call,
                    ast.Attribute, ast.Subscript,
                )):
                    raise ExpressionSecurityError(
                        f"Blocked node type {type(node).__name__} in expression"
                    )
            # Evaluate in a restricted namespace
            result = eval(
                compile(tree, "<expression>", "eval"),
                {"__builtins__": {}},
                _SAFE_FUNCTIONS,
            )
            return bool(result)
        except ExpressionSecurityError:
            raise
        except Exception as e:
            raise ExpressionEvalError(f"Expression eval failed: {e}") from e
