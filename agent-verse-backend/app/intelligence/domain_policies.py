"""
Per-Domain Content Policies
==============================
Domain-specific guardrail rules that activate based on the agent's domain.

Healthcare: block PHI in outputs, require HITL for patient data access
Legal: block client names without consent, require citation
Finance: block specific numbers from being emailed, require reconciliation
Government: require human approval for all submissions
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

try:
    from app.observability.logging import get_logger
    logger = get_logger(__name__)
except Exception:
    import logging
    logger = logging.getLogger(__name__)  # type: ignore[assignment]


@dataclass
class DomainPolicy:
    domain: str
    blocked_output_patterns: list[str] = field(default_factory=list)
    required_output_patterns: list[str] = field(default_factory=list)
    masked_fields: list[str] = field(default_factory=list)
    max_data_payload_bytes: int = 50000  # 50KB default
    require_citation: bool = False
    require_hitl_before_external_send: bool = False
    log_all_tool_calls: bool = True


_DOMAIN_POLICIES: dict[str, DomainPolicy] = {
    "healthcare": DomainPolicy(
        domain="healthcare",
        blocked_output_patterns=[
            r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
            r"\b\d{10,12}\b",           # MRN-like numbers
        ],
        masked_fields=["patient_name", "dob", "ssn", "mrn", "diagnosis"],
        require_citation=True,
        require_hitl_before_external_send=True,
        max_data_payload_bytes=10000,
    ),
    "legal": DomainPolicy(
        domain="legal",
        blocked_output_patterns=[
            r"privilege\s+waived",
            r"without\s+prejudice",  # may need HITL
        ],
        require_citation=True,
        require_hitl_before_external_send=True,
        masked_fields=["client_name", "matter_number"],
    ),
    "banking-fintech": DomainPolicy(
        domain="banking-fintech",
        masked_fields=["account_number", "routing_number", "card_number", "cvv"],
        blocked_output_patterns=[
            r"\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b",  # card numbers
        ],
        max_data_payload_bytes=5000,
        require_hitl_before_external_send=True,
    ),
    "government-portal": DomainPolicy(
        domain="government-portal",
        require_hitl_before_external_send=True,
        log_all_tool_calls=True,
        masked_fields=["aadhaar", "pan", "voter_id"],
    ),
    "gst-tax": DomainPolicy(
        domain="gst-tax",
        require_hitl_before_external_send=True,
        masked_fields=["gstin", "tan", "pan"],
        log_all_tool_calls=True,
    ),
}


def get_domain_policy(domain: str) -> DomainPolicy | None:
    return _DOMAIN_POLICIES.get(domain.lower().replace(" ", "-"))


def apply_domain_policy(
    content: str,
    *,
    domain: str,
    is_output: bool = True,
) -> tuple[str, list[str]]:
    """
    Apply domain policy to content.
    Returns (processed_content, violations).
    """
    policy = get_domain_policy(domain)
    if policy is None:
        return content, []

    violations: list[str] = []
    result = content

    # Mask sensitive fields
    for field_name in policy.masked_fields:
        # Simple regex masking for field=value patterns
        pattern = re.compile(
            rf"({re.escape(field_name)}\s*[=:]\s*)([^\s,\n;]+)",
            re.I,
        )
        result = pattern.sub(r"\1[REDACTED]", result)

    # Check blocked output patterns
    if is_output:
        for pattern_str in policy.blocked_output_patterns:
            if re.search(pattern_str, result):
                violations.append(f"Output contains blocked pattern for domain '{domain}'")
                result = re.sub(pattern_str, "[REDACTED-DOMAIN-POLICY]", result)

    if violations:
        with contextlib.suppress(Exception):
            logger.warning("domain_policy_applied", domain=domain, violations=violations[:3])

    return result, violations
