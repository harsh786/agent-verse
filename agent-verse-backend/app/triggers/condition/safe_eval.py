"""Dependency-free, fail-closed evaluator for trigger condition expressions.

Used whenever ``cel-python`` is not installed (the default deployment). Before
this existed the fallback treated every expression as TRUE, so every event fired
every CONDITION trigger.

Supported (a safe subset of CEL, also accepting the Python spellings):

* literals: strings, ints, floats, ``true``/``false``/``null`` (and
  ``True``/``False``/``None``), list/tuple literals of the above
* names resolved against the payload: ``payload`` is the payload itself; any
  other bare name is a top-level payload key (``severity`` == ``payload.severity``)
* dotted paths and literal-key subscripts — **dict lookups only**:
  ``payload.a.b``, ``payload["a"]["b"]``
* comparisons ``== != < <= > >=``, ``in`` / ``not in`` (chaining allowed)
* ``&&`` / ``and``, ``||`` / ``or``, ``!`` / ``not``, parentheses, unary ``-``

Anything else — calls, attribute access on non-dicts, dunder keys, arithmetic,
comprehensions, lambdas, non-literal subscripts — raises ``ConditionError``, as
does a missing key or a type error during comparison. Callers treat
``ConditionError`` as "condition FALSE" (fail closed).
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from typing import Any

MAX_EXPRESSION_LENGTH = 4096
MAX_AST_NODES = 256

_MISSING = object()

_COMPARE_OPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_NAME_CONSTANTS: dict[str, Any] = {"true": True, "false": False, "null": None}


class ConditionError(ValueError):
    """The expression failed to parse, is not allowed, or failed to evaluate."""


def _cel_to_python(expression: str) -> str:
    """Rewrite CEL ``&&`` / ``||`` / ``!`` to Python outside string literals."""
    out: list[str] = []
    i, n = 0, len(expression)
    quote: str | None = None
    while i < n:
        ch = expression[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(expression[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif expression.startswith("&&", i):
            out.append(" and ")
            i += 2
            continue
        elif expression.startswith("||", i):
            out.append(" or ")
            i += 2
            continue
        elif ch == "!" and not expression.startswith("!=", i):
            out.append(" not ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _lookup(container: Any, key: Any) -> Any:
    if not isinstance(container, Mapping):
        raise ConditionError("field access is only allowed on objects (dicts)")
    if not isinstance(key, str):
        raise ConditionError("only string keys are allowed")
    if key.startswith("__"):
        raise ConditionError(f"access to {key!r} is not allowed")
    value = container.get(key, _MISSING)
    if value is _MISSING:
        raise ConditionError(f"no such key: {key!r}")
    return value


def _eval(node: ast.AST, payload: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, bool | int | float | str):
            return node.value
        raise ConditionError("unsupported literal")
    if isinstance(node, ast.Name):
        if node.id in _NAME_CONSTANTS:
            return _NAME_CONSTANTS[node.id]
        if node.id == "payload":
            return payload
        return _lookup(payload, node.id)
    if isinstance(node, ast.Attribute):
        return _lookup(_eval(node.value, payload), node.attr)
    if isinstance(node, ast.Subscript):
        if not isinstance(node.slice, ast.Constant):
            raise ConditionError("only literal subscripts are allowed")
        return _lookup(_eval(node.value, payload), node.slice.value)
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(elt, payload) for elt in node.elts]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(bool(_eval(v, payload)) for v in node.values)
        return any(bool(_eval(v, payload)) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not bool(_eval(node.operand, payload))
        if isinstance(node.op, ast.USub):
            val = _eval(node.operand, payload)
            if isinstance(val, int | float) and not isinstance(val, bool):
                return -val
        raise ConditionError("unsupported unary operator")
    if isinstance(node, ast.Compare):
        left = _eval(node.left, payload)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            fn = _COMPARE_OPS.get(type(op))
            if fn is None:
                raise ConditionError("unsupported comparison")
            right = _eval(comparator, payload)
            try:
                ok = bool(fn(left, right))
            except TypeError as exc:
                raise ConditionError(f"type error: {exc}") from exc
            if not ok:
                return False
            left = right
        return True
    raise ConditionError(f"{type(node).__name__} is not allowed in a condition")


def parse_condition(expression: str) -> ast.Expression:
    """Parse (and size-check) an expression; raises ``ConditionError``."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ConditionError("expression too long")
    try:
        tree = ast.parse(_cel_to_python(expression).strip(), mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"syntax error: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise ConditionError("expression too complex")
    return tree


_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression, ast.Constant, ast.Name, ast.Attribute, ast.Subscript, ast.List,
    ast.Tuple, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare,
    ast.Load, *_COMPARE_OPS,
)


def check_condition(expression: str) -> None:
    """Raise ``ConditionError`` unless *expression* only uses what
    :func:`evaluate_condition` can run (B1-12): checked when a trigger is
    saved, so an expression that would fail on every fire is refused instead of
    stored. Payload-dependent errors (a missing key) can still occur at fire time.
    """
    if not expression.strip():
        return
    tree = parse_condition(expression)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ConditionError(f"{type(node).__name__} is not allowed in a condition")
        if isinstance(node, ast.Constant) and not (
            node.value is None or isinstance(node.value, bool | int | float | str)
        ):
            raise ConditionError("unsupported literal")
        if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Constant):
            raise ConditionError("only literal subscripts are allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ConditionError("dunder names are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ConditionError("dunder attributes are not allowed")


def evaluate_condition(expression: str, payload: Mapping[str, Any]) -> bool:
    """Evaluate ``expression`` against ``payload``; raises ``ConditionError``.

    An empty expression is TRUE (no condition configured).
    """
    if not expression.strip():
        return True
    tree = parse_condition(expression)
    try:
        return bool(_eval(tree.body, payload))
    except ConditionError:
        raise
    except Exception as exc:  # defensive: never let an eval bug fail open
        raise ConditionError(f"evaluation failed: {exc}") from exc
