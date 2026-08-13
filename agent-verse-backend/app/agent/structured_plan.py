"""Structured execution plan — parses LLM output into topologically sortable steps."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any


class PlanValidationError(ValueError):
    """Raised before execution when a structured plan is unsafe or inconsistent."""


_ALLOWED_CONDITION_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Subscript,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Call,
)

_ALLOWED_STRING_METHODS = frozenset({"endswith", "startswith"})


def _validate_condition(expr: str) -> None:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PlanValidationError("invalid condition expression") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_CONDITION_NODES):
            raise PlanValidationError("invalid condition expression")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise PlanValidationError("invalid condition expression")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Attribute)
            or node.func.attr not in _ALLOWED_STRING_METHODS
            or node.keywords
            or len(node.args) != 1
            or not isinstance(node.args[0], ast.Constant)
            or not isinstance(node.args[0].value, str)
        ):
            raise PlanValidationError("invalid condition expression")


def _safe_eval_condition(expr: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition expression safely using simpleeval or restricted eval.

    Only allows: comparisons, boolean ops, attribute access on step objects,
    string methods, numeric operations. No imports, no arbitrary function calls.
    """
    if not expr or not expr.strip():
        return True

    try:
        # Try simpleeval first (safer AST-based evaluator)
        import simpleeval  # type: ignore[import-not-found]
        evaluator = simpleeval.EvalWithCompoundTypes(names=context)
        return bool(evaluator.eval(expr))
    except ImportError:
        pass

    # Fallback: validate expression before eval using allowlist pattern
    safe_pattern = re.compile(
        r'^[\w\s\.\[\]\'\"=!<>&|+\-\*/%\(\),]+$'
    )
    if not safe_pattern.match(expr):
        import logging
        logging.getLogger(__name__).warning(
            "unsafe_eval_expression_rejected: %s", expr[:100]
        )
        return True  # Default to True (run the step) on unsafe expressions

    # Restricted builtins — no __import__, no open, no exec, no eval
    safe_builtins = {
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict,
        "True": True, "False": False, "None": None,
    }
    try:
        return bool(eval(expr, {"__builtins__": safe_builtins}, context))
    except Exception:
        return True  # Default to True on eval error


@dataclass
class StructuredStep:
    """A single step in a structured execution plan."""

    id: str
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    risk: str = "read"
    expected_output: str = ""
    connector_name: str | None = None
    agent_id: str | None = None
    intent: str = ""
    requires_approval: bool = False
    can_parallel: bool = True
    estimated_minutes: int = 1
    config: dict[str, Any] = field(default_factory=dict)
    # P1.1: Conditional execution and loop fields
    condition: str | None = None          # Python expr: "s1.status == 'complete'"
    loop_until: str | None = None         # Python expr: "output.startswith('SUCCESS')"
    max_loop_iter: int = 5               # Max loop iterations before forced exit
    iterations_used: int = 0            # Tracks how many times we've looped
    # Runtime state (populated during execution)
    status: str = "pending"             # pending | running | complete | failed | skipped
    result: str = ""
    output: str = ""
    error: str | None = None

    @property
    def step_id(self) -> str:
        return self.id

    @property
    def input_from(self) -> list[str]:
        return self.depends_on

    def should_execute(self, step_results: dict[str, StructuredStep]) -> bool:
        """Evaluate condition field. Returns True if step should run."""
        if self.condition is None:
            return True
        try:
            ctx: dict[str, Any] = {}
            for sid, s in step_results.items():
                ctx[sid] = type(
                    "SR", (), {"status": s.status, "output": s.output, "error": s.error}
                )()
            return _safe_eval_condition(self.condition, ctx)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("condition_eval_failed: %s", e)
            return True  # default: run if condition can't be evaluated


@dataclass
class StructuredPlan:
    """An ordered set of :class:`StructuredStep` objects with dependency information."""

    steps: list[StructuredStep] = field(default_factory=list)

    @classmethod
    def from_llm_response(cls, text: str) -> StructuredPlan:
        """Parse an LLM response into a :class:`StructuredPlan`.

        Accepts two formats:
        1. Structured JSON:  ``{"steps": [{...}, ...]}``
        2. Legacy text list: numbered / bulleted lines
        """
        text = text.strip()

        # ── try structured JSON first ──────────────────────────────────────
        try:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                data = json.loads(json_match.group())
                if "steps" in data and isinstance(data["steps"], list):
                    steps: list[StructuredStep] = []
                    for raw in data["steps"]:
                        if isinstance(raw, dict):
                            steps.append(
                                StructuredStep(
                                    id=str(raw.get("id", f"s{len(steps) + 1}")),
                                    description=str(raw.get("description", "")),
                                    tool=raw.get("tool") or None,
                                    arguments=dict(raw.get("arguments") or {}),
                                    depends_on=list(raw.get("depends_on") or []),
                                    risk=str(raw.get("risk", "read")),
                                    expected_output=str(raw.get("expected_output", "")),
                                    # P1.1: condition and loop fields
                                    condition=raw.get("condition") or None,
                                    loop_until=raw.get("loop_until") or None,
                                    max_loop_iter=int(raw.get("max_loop_iter", 5)),
                                )
                            )
                        elif isinstance(raw, str):
                            # Legacy string inside a JSON steps array
                            steps.append(
                                StructuredStep(id=f"s{len(steps) + 1}", description=raw)
                            )
                    plan = cls(steps=steps)
                    plan.validate()
                    return plan
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # ── legacy: plain text list ────────────────────────────────────────
        steps = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            # Strip leading markers: "1.", "- ", "Step 1:", etc.
            line = re.sub(
                r"^(step\s*\d+:?|\d+[\.\)]\s*|-\s*)",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            if line:
                steps.append(StructuredStep(id=f"s{i + 1}", description=line))

        plan = cls(steps=steps)
        plan.validate()
        return plan

    def validate(self) -> StructuredPlan:
        """Validate identifiers, dependencies, loops, conditions, and acyclicity."""
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise PlanValidationError("duplicate step id")
        known_ids = set(ids)
        for step in self.steps:
            if not step.id or not step.description:
                raise PlanValidationError("step id and description are required")
            if step.id in step.depends_on:
                raise PlanValidationError("step cannot depend on itself")
            unknown = set(step.depends_on) - known_ids
            if unknown:
                raise PlanValidationError(f"unknown dependencies: {sorted(unknown)}")
            if step.max_loop_iter <= 0:
                raise PlanValidationError("loop limit must be positive")
            if step.condition:
                _validate_condition(step.condition)
            if step.loop_until:
                _validate_condition(step.loop_until)

        indegree = {step.id: len(set(step.depends_on)) for step in self.steps}
        dependents: dict[str, list[str]] = {step_id: [] for step_id in known_ids}
        for step in self.steps:
            for dependency in set(step.depends_on):
                dependents[dependency].append(step.id)
        ready = [step_id for step_id, count in indegree.items() if count == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for dependent in dependents[current]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        if visited != len(self.steps):
            raise PlanValidationError("dependency cycle detected")
        return self

    def execution_waves(self) -> list[list[StructuredStep]]:
        """Return steps grouped into topological execution waves.

        Steps within the same wave have no un-met dependencies and can
        execute in parallel.  Each successive wave depends on all prior waves
        having completed.
        """
        self.validate()
        if not self.steps:
            return []

        completed: set[str] = set()
        remaining = list(self.steps)
        waves: list[list[StructuredStep]] = []

        while remaining:
            wave = [s for s in remaining if all(dep in completed for dep in s.depends_on)]

            waves.append(wave)
            completed.update(s.id for s in wave)
            remaining = [s for s in remaining if s.id not in completed]

        return waves

    def to_step_list(self) -> list[str]:
        """Backward-compatible conversion to ``list[str]`` of step descriptions."""
        return [s.description for s in self.steps]
