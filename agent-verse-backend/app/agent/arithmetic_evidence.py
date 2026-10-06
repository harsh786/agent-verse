"""Deterministic arithmetic evidence for the grounding gates (B7 live open item 1).

The grounding gates check an answer's concrete claims (numbers, ids, ...) against
the evidence gathered while executing: tool outputs, retrieved knowledge and the
goal text. A computed answer has no such evidence: "391" for "What is 17*23?"
appears in no tool output and not in the goal, so the keyword gate flagged it as
an ungrounded claim (and replanned it on a high-risk goal) although it is
correct.

This module recomputes every plain arithmetic expression found in the goal and
the agent's own step texts (``17*23``, ``17 x 23``, ``(3 + 4) * 2``,
``17 times 23``) with a restricted evaluator and renders the results as evidence
("17*23 = 391"). Nothing the model claims is trusted: a correct result is
grounded by the recomputation, a wrong one ("392") is not, and
:func:`wrong_results` reports an answer that states a result the recomputation
contradicts.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

_MAX_EXPR_CHARS = 120
_MAX_FACTS = 20
_MAX_ABS_RESULT = 1e18
_MAX_POW_EXPONENT = 64
_MAX_POW_BASE = 1e6

_WORD_OPERATORS = (
    (re.compile(r"\bmultiplied\s+by\b", re.I), "*"),
    (re.compile(r"\btimes\b", re.I), "*"),
    (re.compile(r"\bdivided\s+by\b", re.I), "/"),
    (re.compile(r"\bplus\b", re.I), "+"),
    (re.compile(r"\bminus\b", re.I), "-"),
    (re.compile(r"\bto\s+the\s+power\s+of\b", re.I), "^"),
)
# Unicode operators (escaped: multiplication sign, multiplication x, middle dot,
# division sign, minus sign, en dash).
_SYMBOLS = str.maketrans(
    {
        "\u00d7": "*",
        "\u2715": "*",
        "\u00b7": "*",
        "\u00f7": "/",
        "\u2212": "-",
        "\u2013": "-",
    }
)
# "17 x 23" / "17X23": an x between two numbers is a multiplication sign.
_X_TIMES = re.compile(r"(?<=\d)\s*[xX]\s*(?=[\d(])")
# Thousands separators only ("1,024" -> "1024"); "3,4" stays a list.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# A run of numbers, operators, parentheses and spaces with at least one operator
# between two operands. Not part of a larger token (ids like "SUP-12", versions).
_CANDIDATE = re.compile(
    r"(?<![\w.\-/])"
    r"(\(*\s*-?\d+(?:\.\d+)?(?:\s*\)*\s*(?:\*\*|[-+*/^%])\s*\(*\s*-?\d+(?:\.\d+)?)+\s*\)*)"
    r"(?!\w|\.\d)"
)
_DATE_LIKE = re.compile(r"^\d{1,4}([-/.])\d{1,2}\1\d{1,4}$")
_PHONE_LIKE = re.compile(r"^\d{3}-\d{3,4}(?:-\d{4})?$")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\w])")
_EQUATION_TAIL = re.compile(r"^\s*(?:=|==|equals|is)\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)", re.I)

_ALLOWED_BINOPS: dict[type[ast.operator], object] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: None,  # bounded separately
}


@dataclass(frozen=True)
class ArithmeticFact:
    """One recomputed expression: ``expression`` (normalised) = ``value``."""

    expression: str
    value: float

    @property
    def rendered(self) -> str:
        return render_number(self.value)

    def evidence(self) -> str:
        r = self.rendered
        return (
            f"{self.expression} = {r} (the result / answer / total of {self.expression} "
            f"equals {r})"
        )


def render_number(value: float) -> str:
    """``391.0`` -> ``"391"``, ``2.5`` -> ``"2.5"``, ``1/3`` -> ``"0.3333333333"``."""
    if float(value).is_integer():
        return str(int(value))
    return format(value, ".10g")


def _normalise(text: str) -> str:
    out = text.translate(_SYMBOLS)
    for pattern, symbol in _WORD_OPERATORS:
        out = pattern.sub(f" {symbol} ", out)
    out = _X_TIMES.sub("*", out)
    return _THOUSANDS.sub("", out)


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_POW_EXPONENT or abs(left) > _MAX_POW_BASE:
                raise ValueError("exponent out of range")
            result = left**right
        else:
            fn = _ALLOWED_BINOPS[type(node.op)]
            result = fn(left, right)  # type: ignore[operator]
        if isinstance(result, complex) or not math.isfinite(result):
            raise ValueError("not a finite real number")
        if abs(result) > _MAX_ABS_RESULT:
            raise ValueError("result out of range")
        return float(result)
    raise ValueError(f"unsupported node {type(node).__name__}")


def _evaluate_expression(expr: str) -> float | None:
    source = expr.replace("^", "**")
    if len(source) > _MAX_EXPR_CHARS:
        return None
    try:
        return _evaluate(ast.parse(source, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        return None


def _candidates(text: str) -> Iterable[tuple[str, int]]:
    """``(normalised expression, end offset in the normalised text)`` pairs."""
    for match in _CANDIDATE.finditer(text):
        raw = match.group(1).strip()
        compact = re.sub(r"\s+", "", raw)
        # Balanced parentheses only (the pattern may clip one side).
        if compact.count("(") != compact.count(")"):
            compact = compact.strip("()")
            if compact.count("(") != compact.count(")"):
                continue
        # Dates and phone numbers, also when written inside parentheses.
        bare = compact.strip("()")
        if _DATE_LIKE.match(bare) or _PHONE_LIKE.match(bare):
            continue
        yield compact, match.end(1)


def derive_arithmetic(texts: Iterable[str]) -> list[ArithmeticFact]:
    """Recompute every arithmetic expression in ``texts`` (deduplicated, capped)."""
    facts: dict[str, ArithmeticFact] = {}
    for text in texts:
        if not text:
            continue
        for expr, _ in _candidates(_normalise(str(text))):
            if expr in facts:
                continue
            value = _evaluate_expression(expr)
            if value is not None:
                facts[expr] = ArithmeticFact(expr, value)
            if len(facts) >= _MAX_FACTS:
                return list(facts.values())
    return list(facts.values())


def render_evidence(facts: Iterable[ArithmeticFact]) -> str:
    """One evidence chunk: every recomputed expression, one per line."""
    return "\n".join(fact.evidence() for fact in facts)


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) or render_number(a) == render_number(b)


def _parse_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def wrong_results(answer: str, goal: str = "") -> list[str]:
    """Results the answer states that the recomputation contradicts.

    * an equation in the answer whose stated result is wrong ("17 x 23 = 392");
    * a short answer to a goal holding exactly one expression whose only new
      number differs from that expression's value ("392" for "What is 17*23?").
    """
    wrong: list[str] = []
    norm = _normalise(answer or "")
    for expr, end in _candidates(norm):
        tail = _EQUATION_TAIL.match(norm[end:])
        if tail is None:
            continue
        value, stated = _evaluate_expression(expr), _parse_number(tail.group(1))
        if value is not None and stated is not None and not _same(value, stated):
            wrong.append(tail.group(1))
    if wrong:
        return list(dict.fromkeys(wrong))
    goal_facts = derive_arithmetic([goal])
    if len(goal_facts) != 1 or len((answer or "").split()) > 12:
        return []
    goal_numbers = {n.replace(",", "") for n in _NUMBER.findall(_normalise(goal))}
    new_numbers = [
        n for n in _NUMBER.findall(answer or "") if n.replace(",", "") not in goal_numbers
    ]
    if len(new_numbers) != 1:
        return []
    stated = _parse_number(new_numbers[0])
    if stated is None or _same(goal_facts[0].value, stated):
        return []
    return [new_numbers[0]]
