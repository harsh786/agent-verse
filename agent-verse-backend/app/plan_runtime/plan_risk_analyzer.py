from __future__ import annotations
import re

_CRITICAL = [
    (re.compile(r"\b(delete|drop|truncate|wipe|purge|destroy)\b", re.I), "destructive operation"),
    (re.compile(r"\b(production|prod)\b.*\b(deploy|delete|migrate|drop)\b", re.I), "production mutation"),
    (re.compile(r"\bcharge\b|\btransfer funds\b|\bpayment\b", re.I), "financial operation"),
]
_HIGH = [
    (re.compile(r"\b(deploy|migrate|alter|publish|release)\b", re.I), "state-changing operation"),
    (re.compile(r"\b(send email|send sms|notify all|broadcast)\b", re.I), "external notification"),
    (re.compile(r"\b(grant admin|revoke access|disable account)\b", re.I), "access control change"),
]


class PlanRiskAnalyzer:
    def analyze(self, plan: list[str]) -> tuple[str, list[str]]:
        findings: list[str] = []
        max_risk = "low"
        for step in plan:
            for pattern, label in _CRITICAL:
                if pattern.search(step):
                    findings.append(f"CRITICAL: {label} in step: {step[:80]}")
                    max_risk = "critical"
            for pattern, label in _HIGH:
                if pattern.search(step):
                    findings.append(f"HIGH: {label} in step: {step[:80]}")
                    if max_risk not in ("critical", "high"):
                        max_risk = "high"
        return max_risk, findings
