"""CORE-25: documented runtime trade-offs have config switches (defaults unchanged).

* GUARDRAIL_FAIL_CLOSED_ALL — an errored guardrail blocks every step, not only
  high-risk ones.
* RPA_INTERACTION_RISK — risk class for declared-"high" interactive RPA tools
  (rpa_click / rpa_type ...): write_low (default) or write_high (approval-gated).
* EXPLORE_RATE_UNKNOWN — how often an unknown model probes richer execution modes.
"""

from __future__ import annotations

import pytest

from app.agent.nodes._helpers import _guardrail_should_fail_closed
from app.agent.tool_risk import classify_tool_risk
from app.core.config import get_settings


class _FreshSettings:
    """get_settings() is lru-cached: re-read the env after each setenv."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mp = monkeypatch

    def set(self, name: str, value: str | None) -> None:
        if value is None:
            self._mp.delenv(name, raising=False)
        else:
            self._mp.setenv(name, value)
        get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> object:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_guardrail_default_fails_closed_only_on_high_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    _FreshSettings(monkeypatch).set("GUARDRAIL_FAIL_CLOSED_ALL", None)
    assert _guardrail_should_fail_closed("summarize the notes") is False
    assert _guardrail_should_fail_closed("delete the table") is True


def test_guardrail_fail_closed_all_blocks_low_risk_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _FreshSettings(monkeypatch).set("GUARDRAIL_FAIL_CLOSED_ALL", "true")
    assert _guardrail_should_fail_closed("summarize the notes") is True


def test_rpa_interaction_risk_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _FreshSettings(monkeypatch).set("RPA_INTERACTION_RISK", None)
    assert classify_tool_risk("rpa_click") == "write_low"
    _FreshSettings(monkeypatch).set("RPA_INTERACTION_RISK", "write_high")
    assert classify_tool_risk("rpa_click") == "write_high"
    assert classify_tool_risk("rpa_type") == "write_high"
    # Read-only RPA tools are unaffected.
    assert classify_tool_risk("rpa_screenshot") == "read"


def test_explore_rate_unknown_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.execution_strategy import profile_for
    from app.agent.strategy_adaptivity import _explore_probability

    profile = profile_for("totally-unknown-model-xyz")
    _FreshSettings(monkeypatch).set("EXPLORE_RATE_UNKNOWN", None)
    assert _explore_probability(profile, already_capable=False) == 1.0
    _FreshSettings(monkeypatch).set("EXPLORE_RATE_UNKNOWN", "0.25")
    assert _explore_probability(profile, already_capable=False) == 0.25
