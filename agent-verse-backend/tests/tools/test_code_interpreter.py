"""Tests for CodeInterpreter."""
from __future__ import annotations

import pytest
from app.tools.code_interpreter import CodeInterpreter, get_interpreter


@pytest.mark.asyncio
async def test_execute_hello_world():
    interp = CodeInterpreter(timeout=10)
    result = await interp.execute("print('hello world')", "python")
    assert isinstance(result.success, bool)


@pytest.mark.asyncio
async def test_execute_python_stdout():
    interp = CodeInterpreter(timeout=10)
    result = await interp.execute("print('hello from sandbox')", "python")
    assert result.success is True
    assert "hello from sandbox" in result.stdout


@pytest.mark.asyncio
async def test_execute_accepts_tenant_id():
    """execute() must accept optional tenant_id kwarg without error."""
    interp = CodeInterpreter(timeout=10)
    result = await interp.execute("print(1)", "python", tenant_id="t-tenant")
    assert isinstance(result.success, bool)


@pytest.mark.asyncio
async def test_unsupported_language():
    interp = CodeInterpreter()
    result = await interp.execute("code", "ruby")
    assert result.success is False
    assert "Unsupported language" in result.stderr


@pytest.mark.asyncio
async def test_exit_code_on_error():
    interp = CodeInterpreter(timeout=10)
    result = await interp.execute("raise ValueError('boom')", "python")
    assert result.exit_code != 0
    assert result.success is False


def test_docker_check():
    result = CodeInterpreter._check_docker()
    assert isinstance(result, bool)


def test_get_interpreter_factory():
    interp = get_interpreter(timeout=15)
    assert isinstance(interp, CodeInterpreter)
    assert interp._timeout == 15
