import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
REQUIRED_ALERTS = {
    "CoordinationDeliveryLag", "CoordinationLeaseReclaims", "AgentPatternRunaway",
    "SandboxDenialOutage", "MagenticStalls", "AuctionAnomalies",
    "ReflexionQualityPoisoning", "TenantPolicyAnomalies",
}
REQUIRED_SECTIONS = {
    "## Symptoms", "## Impact", "## Dashboard queries", "## Diagnosis",
    "## Mitigation", "## Recovery", "## Verification", "## Escalation", "## Rollback",
}


def test_alerts_link_to_actionable_repository_runbooks() -> None:
    groups = yaml.safe_load((ROOT / "infra/prometheus/alerts.yml").read_text())["groups"]
    rules = [rule for group in groups for rule in group["rules"]]
    by_name = {rule["alert"]: rule for rule in rules}
    assert by_name.keys() >= REQUIRED_ALERTS
    for name in REQUIRED_ALERTS:
        link = by_name[name]["annotations"]["runbook"]
        assert link.startswith("infra/runbooks/")
        content = (ROOT / link).read_text()
        assert {line.strip() for line in content.splitlines()} >= REQUIRED_SECTIONS


def test_two_agent_pattern_dashboards_are_valid_and_distinct() -> None:
    directory = ROOT / "infra/grafana/dashboards"
    health = json.loads((directory / "agent-pattern-platform-health.json").read_text())
    quality = json.loads((directory / "agent-pattern-quality-outcomes.json").read_text())
    assert health["uid"] != quality["uid"]
    assert len(health["panels"]) >= 6
    assert len(quality["panels"]) >= 6
