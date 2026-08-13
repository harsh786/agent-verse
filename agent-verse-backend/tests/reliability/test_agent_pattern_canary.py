from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_switch_requires_gate_and_keeps_previous_color_warm() -> None:
    script = (ROOT / "infra/k8s/switch-traffic.sh").read_text()
    assert "check_agent_pattern_canary.py" in script
    assert "OBSERVATION_WINDOW_SECONDS" in script
    assert '--replicas=0' not in script
    assert "rollback" in script.lower()


def test_rollout_configuration_has_scoped_controls() -> None:
    values = (ROOT / "helm/agentverse/values.yaml").read_text()
    keys = (
        "tenantAllowlist", "familyFlags", "adapterVersionPins", "killSwitch",
        "cohortPercentage",
    )
    for key in keys:
        assert key in values
