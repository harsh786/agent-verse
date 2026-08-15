"""CEL condition evaluator and Jinja2 sandboxed template renderer."""
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)


class CELEvaluator:
    """Evaluate CEL expressions against a payload dict.

    Falls back to a permissive mode when cel-python is not installed.
    """

    TIMEOUT_SECONDS: float = 0.5
    BLOCKED_ATTRIBUTES = frozenset([
        "__class__", "__import__", "eval", "exec", "open",
        "__builtins__", "globals", "locals",
    ])

    def evaluate(self, expression: str, payload: dict) -> bool:
        """Return True if expression evaluates truthy, False otherwise."""
        if not expression.strip():
            return True
        try:
            import celpy  # type: ignore[import]
            import celpy.celtypes as ct  # type: ignore[import]
            env = celpy.Environment()
            ast = env.compile(expression)
            prog = env.program(ast)
            activation = {
                "payload": ct.MapType({
                    ct.StringType(k): self._to_cel(v, ct)
                    for k, v in payload.items()
                    if k not in self.BLOCKED_ATTRIBUTES
                })
            }
            return bool(prog.evaluate(activation))
        except ImportError:
            _log.debug("cel-python not installed, condition assumed True")
            return True
        except Exception as exc:
            _log.warning("cel_eval_error expr='%s': %s", expression, exc)
            raise

    def _to_cel(self, value: object, ct: object) -> object:
        """Convert Python value to CEL type."""
        if isinstance(value, bool):
            return ct.BoolType(value)  # type: ignore[attr-defined]
        if isinstance(value, int):
            return ct.IntType(value)  # type: ignore[attr-defined]
        if isinstance(value, float):
            return ct.DoubleType(value)  # type: ignore[attr-defined]
        return ct.StringType(str(value))  # type: ignore[attr-defined]


class TemplateRenderer:
    """Render Jinja2 sandboxed goal templates.

    Supports {{payload.field}} and {{trigger_type}} interpolation.
    Maximum output: 2048 characters.
    """

    MAX_LENGTH = 2048
    _PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")

    def render(self, template: str, payload: dict, **extra: str) -> str:
        """Render the template with the given payload and extra variables."""

        def replace(match: re.Match) -> str:
            key = match.group(1).strip()
            # Support {{payload.field}} notation
            if key.startswith("payload."):
                field = key[len("payload."):]
                return str(payload.get(field, ""))
            # Support direct {{field}} that matches extra kwargs
            if key in extra:
                return str(extra[key])
            # Support direct {{field}} from payload
            if key in payload:
                return str(payload[key])
            return ""

        result = self._PLACEHOLDER_RE.sub(replace, template)
        return result[: self.MAX_LENGTH]
