"""Tests for ExpressionEngine — safe expression evaluation."""
from __future__ import annotations

import pytest
from app.workflow.expression_engine import (
    ExpressionEngine,
    ExpressionSecurityError,
    ExpressionEvalError,
)


@pytest.fixture
def engine():
    return ExpressionEngine()


# ── Arithmetic & Comparison ───────────────────────────────────────────────────

def test_simple_gt(engine):
    assert engine.evaluate("1 > 0") is True

def test_simple_lt(engine):
    assert engine.evaluate("5 < 3") is False

def test_equality(engine):
    assert engine.evaluate("1 == 1") is True

def test_not_equal(engine):
    assert engine.evaluate("'a' != 'b'") is True

def test_ge(engine):
    assert engine.evaluate("0.8 >= 0.8") is True

def test_arithmetic_expression(engine):
    assert engine.evaluate("2 + 3 > 4") is True


# ── Logical Operators ─────────────────────────────────────────────────────────

def test_and_true(engine):
    assert engine.evaluate("True and True") is True

def test_and_false(engine):
    assert engine.evaluate("True and False") is False

def test_or_true(engine):
    assert engine.evaluate("False or True") is True

def test_not_false(engine):
    assert engine.evaluate("not False") is True


# ── Membership ────────────────────────────────────────────────────────────────

def test_in_list(engine):
    assert engine.evaluate("'a' in ['a', 'b']") is True

def test_not_in_list(engine):
    assert engine.evaluate("'c' not in ['a', 'b']") is True


# ── Safe functions ────────────────────────────────────────────────────────────

def test_len_function(engine):
    assert engine.evaluate("len('hello') == 5") is True

def test_lower_function(engine):
    assert engine.evaluate("lower('HELLO') == 'hello'") is True

def test_contains_function(engine):
    assert engine.evaluate("contains('hello world', 'world')") is True

def test_round_function(engine):
    assert engine.evaluate("round(0.731, 1) == 0.7") is True


# ── Blocked constructs ────────────────────────────────────────────────────────

def test_blocked_import(engine):
    with pytest.raises(ExpressionSecurityError):
        engine.evaluate("import os")

def test_blocked_exec(engine):
    with pytest.raises(ExpressionSecurityError):
        engine.evaluate("exec('print(1)')")

def test_blocked_builtins(engine):
    with pytest.raises(ExpressionSecurityError):
        engine.evaluate("builtins.open('file')")

def test_blocked_os(engine):
    with pytest.raises(ExpressionSecurityError):
        engine.evaluate("os.path.exists('/')")

def test_blocked_getattr(engine):
    with pytest.raises(ExpressionSecurityError):
        engine.evaluate("getattr(object, '__class__')")


# ── Context variables (pre-resolved) ─────────────────────────────────────────

def test_resolved_number_comparison(engine):
    # ContextResolver resolves {{steps.x.output.score}} → 0.73 before eval
    assert engine.evaluate("0.73 > 0.4") is True
    assert engine.evaluate("0.73 > 0.8") is False
