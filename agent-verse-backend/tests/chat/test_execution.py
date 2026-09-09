"""Tests for inline code execution — 10 cases."""

from __future__ import annotations

import sys

import pytest

from app.chat.execution import SUPPORTED_LANGUAGES, ChatCodeExecutor


@pytest.fixture()
def executor() -> ChatCodeExecutor:
    return ChatCodeExecutor()


def test_execute_python_hello_world(executor: ChatCodeExecutor) -> None:
    result = executor.execute("print('hello world')", "python", "s1")
    assert result.exit_code == 0
    assert "hello" in result.stdout


def test_execute_python_arithmetic(executor: ChatCodeExecutor) -> None:
    result = executor.execute("print(2 + 2)", "python", "s1")
    assert result.exit_code == 0
    assert "4" in result.stdout


def test_execute_python_syntax_error(executor: ChatCodeExecutor) -> None:
    result = executor.execute("def broken(:", "python", "s1")
    assert result.exit_code != 0


def test_execute_python_runtime_error(executor: ChatCodeExecutor) -> None:
    result = executor.execute("1/0", "python", "s1")
    assert result.exit_code != 0
    assert "ZeroDivisionError" in result.stderr or result.exit_code != 0


def test_execute_bash_echo(executor: ChatCodeExecutor) -> None:
    result = executor.execute("echo 'bash works'", "bash", "s1")
    assert result.exit_code == 0
    assert "bash works" in result.stdout


def test_execute_unsupported_language_returns_error(executor: ChatCodeExecutor) -> None:
    result = executor.execute("SELECT 1", "sql", "s1")
    assert result.exit_code == 1
    assert result.error == "unsupported_language"


def test_execute_timeout(executor: ChatCodeExecutor) -> None:
    result = executor.execute("import time; time.sleep(60)", "python", "s1", timeout=1)
    assert result.error == "timeout"
    assert result.exit_code == 124


def test_execute_records_duration(executor: ChatCodeExecutor) -> None:
    result = executor.execute("print('hi')", "python", "s1")
    assert result.duration_ms >= 0


def test_execute_language_alias_py(executor: ChatCodeExecutor) -> None:
    result = executor.execute("print('alias')", "py", "s1")
    assert result.exit_code == 0


def test_execute_stdout_stderr_separation(executor: ChatCodeExecutor) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr)"
    result = executor.execute(code, "python", "s1")
    assert "out" in result.stdout
    assert "err" in result.stderr
