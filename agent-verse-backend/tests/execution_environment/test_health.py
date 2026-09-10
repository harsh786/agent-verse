"""Tests for health-check interfaces."""
from __future__ import annotations

from app.execution_environment.health import (
    AlwaysHealthyCheck,
    AlwaysUnhealthyCheck,
)


async def test_always_healthy_returns_healthy() -> None:
    check = AlwaysHealthyCheck(runner_type="fake")
    status = await check.check()
    assert status.healthy is True
    assert status.runner_type == "fake"


async def test_always_unhealthy_returns_unhealthy() -> None:
    check = AlwaysUnhealthyCheck(runner_type="local", message="service down")
    status = await check.check()
    assert status.healthy is False
    assert status.runner_type == "local"
    assert "service down" in status.message


async def test_always_unhealthy_default_message() -> None:
    check = AlwaysUnhealthyCheck(runner_type="kubernetes")
    status = await check.check()
    assert status.healthy is False
    assert "kubernetes" in status.message.lower()
