# AgentVerse Core Dynamic Orchestration — Part 2: Security & Safety Gates (P0)

> **Depends on:** Part 1 (orchestration runtime_profile, strategy_registry, goal_classifier, pattern_selector must exist)

**Goal:** Implement the six P0 safety layers that must be in place before any dynamic routing reaches graph execution: SecurityRuntime, PolicyRuntime compiler, DataClassification, CapabilityRegistry, PlanRuntime verifier, and RuntimeReadiness gate.

**Run all tests in this phase:**
```bash
cd agent-verse-backend
uv run pytest tests/security_runtime/ tests/policy_runtime/ tests/data_classification/ \
    tests/capabilities/ tests/plan_runtime/ tests/runtime_readiness/ -v --no-cov
```

---

## Task 7: SecurityRuntime — GuardrailProfile + GovernanceProfile

**Files:**
- Create: `app/security_runtime/__init__.py`
- Create: `app/security_runtime/guardrail_profile.py`
- Create: `app/security_runtime/governance_profile.py`
- Create: `app/security_runtime/policy_bundle_selector.py`
- Create: `tests/security_runtime/__init__.py`
- Create: `tests/security_runtime/test_guardrail_profile.py`

- [ ] **Step 7.1: Write failing tests**

```python
# tests/security_runtime/test_guardrail_profile.py
"""SecurityRuntime must produce correct profiles for all risk levels."""
from __future__ import annotations
import pytest
from app.security_runtime.guardrail_profile import GuardrailProfileSelector, GuardrailBundle
from app.security_runtime.governance_profile import GovernanceProfileSelector, GovernanceBundle
from app.security_runtime.policy_bundle_selector import PolicyBundleSelector
from app.orchestration.runtime_profile import GoalRuntimeProfile, GoalProperties, SecurityConfig
from app.orchestration.runtime_profile import (
    AgentPatternConfig, RAGStrategyConfig, ModelPlanConfig, MemoryCacheConfig, EvalConfig,
    RiskLevel, Complexity,
)
from app.tenancy.context import TenantContext, PlanTier


def _make_profile(risk: RiskLevel = RiskLevel.LOW, hitl: bool = False) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=risk),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=hitl),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def _make_tenant(plan: PlanTier = PlanTier.PROFESSIONAL) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=plan, api_key_id="k1")


# GuardrailProfile tests
def test_low_risk_gets_default_bundle():
    selector = GuardrailProfileSelector()
    profile = _make_profile(RiskLevel.LOW)
    bundle = selector.select(profile, tenant_ctx=_make_tenant())
    assert bundle.name == GuardrailBundle.DEFAULT


def test_high_risk_gets_strict_bundle():
    selector = GuardrailProfileSelector()
    profile = _make_profile(RiskLevel.HIGH)
    bundle = selector.select(profile, tenant_ctx=_make_tenant())
    assert bundle.name == GuardrailBundle.STRICT
    assert bundle.scan_prompt_injection is True
    assert bundle.scan_output_pii is True


def test_critical_risk_gets_strict_with_exfil():
    selector = GuardrailProfileSelector()
    profile = _make_profile(RiskLevel.CRITICAL)
    bundle = selector.select(profile, tenant_ctx=_make_tenant())
    assert bundle.name == GuardrailBundle.STRICT
    assert bundle.exfiltration_guard_enabled is True


def test_regulated_tenant_gets_regulated_bundle():
    selector = GuardrailProfileSelector()
    profile = _make_profile(RiskLevel.LOW)
    profile.security.compliance_tags.append("gdpr")
    bundle = selector.select(profile, tenant_ctx=_make_tenant())
    assert bundle.name == GuardrailBundle.REGULATED
    assert bundle.pii_redaction_enabled is True


# GovernanceProfile tests
def test_free_plan_gets_free_governance():
    selector = GovernanceProfileSelector()
    profile = _make_profile()
    gov = selector.select(profile, tenant_ctx=_make_tenant(PlanTier.FREE))
    assert gov.name == GovernanceBundle.FREE
    assert gov.cost_control_enabled is True


def test_enterprise_gets_full_governance():
    selector = GovernanceProfileSelector()
    profile = _make_profile(RiskLevel.CRITICAL, hitl=True)
    gov = selector.select(profile, tenant_ctx=_make_tenant(PlanTier.ENTERPRISE))
    assert gov.name == GovernanceBundle.ENTERPRISE
    assert gov.hitl_enabled is True
    assert gov.policy_engine_enabled is True


# PolicyBundleSelector tests
def test_policy_bundle_allows_low_risk():
    selector = PolicyBundleSelector()
    profile = _make_profile(RiskLevel.LOW)
    bundle = selector.select(profile, tenant_ctx=_make_tenant())
    assert bundle.audit_level == "standard"
    assert bundle.max_cost_usd > 0


def test_policy_bundle_critical_forces_forensic_audit():
    selector = PolicyBundleSelector()
    profile = _make_profile(RiskLevel.CRITICAL)
    bundle = selector.select(profile, tenant_ctx=_make_tenant())
    assert bundle.audit_level == "forensic"
    assert bundle.required_approvals == ["hitl"]
```

- [ ] **Step 7.2: Create directories and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/security_runtime
mkdir -p agent-verse-backend/tests/security_runtime
touch agent-verse-backend/app/security_runtime/__init__.py
touch agent-verse-backend/tests/security_runtime/__init__.py
cd agent-verse-backend && uv run pytest tests/security_runtime/test_guardrail_profile.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 7.3: Implement `app/security_runtime/guardrail_profile.py`**

```python
"""GuardrailProfileSelector — selects the right guardrail bundle per runtime profile."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


class GuardrailBundle(str, enum.Enum):
    DEFAULT = "default"
    STRICT = "strict"
    REGULATED = "regulated"
    DEVELOPER = "developer"
    RPA = "rpa"


@dataclass
class GuardrailConfig:
    name: GuardrailBundle
    scan_prompt_injection: bool = True
    scan_output_pii: bool = False
    scan_toxicity: bool = True
    exfiltration_guard_enabled: bool = False
    pii_redaction_enabled: bool = False
    output_schema_validation: bool = False
    block_on_injection: bool = True
    block_on_pii: bool = False
    max_output_tokens: int = 8000
    enabled_scanners: list[str] = field(default_factory=lambda: ["injection", "toxicity"])


class GuardrailProfileSelector:
    """Selects a GuardrailConfig based on risk level, compliance tags, and tenant context."""

    def select(
        self,
        profile: "GoalRuntimeProfile",
        *,
        tenant_ctx: "TenantContext",
    ) -> GuardrailConfig:
        from app.orchestration.runtime_profile import RiskLevel

        risk = profile.properties.risk
        compliance = profile.security.compliance_tags

        # Regulated tenants always get regulated bundle
        regulated_tags = {"gdpr", "hipaa", "pci", "soc2", "dpdp", "sox"}
        if compliance and set(compliance) & regulated_tags:
            return GuardrailConfig(
                name=GuardrailBundle.REGULATED,
                scan_prompt_injection=True,
                scan_output_pii=True,
                scan_toxicity=True,
                exfiltration_guard_enabled=True,
                pii_redaction_enabled=True,
                output_schema_validation=True,
                block_on_injection=True,
                block_on_pii=True,
                enabled_scanners=["injection", "toxicity", "pii", "exfiltration", "schema"],
            )

        if risk == RiskLevel.CRITICAL:
            return GuardrailConfig(
                name=GuardrailBundle.STRICT,
                scan_prompt_injection=True,
                scan_output_pii=True,
                scan_toxicity=True,
                exfiltration_guard_enabled=True,
                pii_redaction_enabled=False,
                output_schema_validation=True,
                block_on_injection=True,
                block_on_pii=False,
                enabled_scanners=["injection", "toxicity", "exfiltration", "schema"],
            )

        if risk == RiskLevel.HIGH:
            return GuardrailConfig(
                name=GuardrailBundle.STRICT,
                scan_prompt_injection=True,
                scan_output_pii=True,
                scan_toxicity=True,
                exfiltration_guard_enabled=False,
                pii_redaction_enabled=False,
                block_on_injection=True,
                enabled_scanners=["injection", "toxicity", "pii"],
            )

        # Default bundle for low/medium risk
        return GuardrailConfig(
            name=GuardrailBundle.DEFAULT,
            scan_prompt_injection=True,
            scan_output_pii=False,
            scan_toxicity=True,
            exfiltration_guard_enabled=False,
            block_on_injection=True,
            enabled_scanners=["injection", "toxicity"],
        )
```

- [ ] **Step 7.4: Implement `app/security_runtime/governance_profile.py`**

```python
"""GovernanceProfileSelector — selects governance bundle per plan tier and risk."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


class GovernanceBundle(str, enum.Enum):
    FREE = "free"
    ENTERPRISE = "enterprise"
    REGULATED = "regulated"


@dataclass
class GovernanceConfig:
    name: GovernanceBundle
    hitl_enabled: bool = False
    cost_control_enabled: bool = True
    policy_engine_enabled: bool = False
    audit_enabled: bool = True
    compliance_reporting_enabled: bool = False
    rbac_enforcement: str = "basic"         # basic|full|delegated
    max_goal_cost_usd: float = 10.0
    allowed_tools: list[str] = field(default_factory=list)   # empty = all allowed


class GovernanceProfileSelector:
    def select(
        self,
        profile: "GoalRuntimeProfile",
        *,
        tenant_ctx: "TenantContext",
    ) -> GovernanceConfig:
        from app.tenancy.context import PlanTier
        from app.orchestration.runtime_profile import RiskLevel

        plan = tenant_ctx.plan
        risk = profile.properties.risk
        hitl = profile.security.hitl_required

        if plan == PlanTier.ENTERPRISE or risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return GovernanceConfig(
                name=GovernanceBundle.ENTERPRISE,
                hitl_enabled=hitl,
                cost_control_enabled=True,
                policy_engine_enabled=True,
                audit_enabled=True,
                compliance_reporting_enabled=True,
                rbac_enforcement="full",
                max_goal_cost_usd=100.0,
            )

        if profile.security.compliance_tags:
            return GovernanceConfig(
                name=GovernanceBundle.REGULATED,
                hitl_enabled=hitl,
                cost_control_enabled=True,
                policy_engine_enabled=True,
                audit_enabled=True,
                compliance_reporting_enabled=True,
                rbac_enforcement="full",
                max_goal_cost_usd=50.0,
            )

        return GovernanceConfig(
            name=GovernanceBundle.FREE,
            hitl_enabled=hitl,
            cost_control_enabled=True,
            policy_engine_enabled=False,
            audit_enabled=True,
            rbac_enforcement="basic",
            max_goal_cost_usd=10.0,
        )
```

- [ ] **Step 7.5: Implement `app/security_runtime/policy_bundle_selector.py`**

```python
"""PolicyBundleSelector — produces a CompiledRuntimePolicy before graph execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


@dataclass
class CompiledRuntimePolicy:
    allowed_capabilities: list[str] = field(default_factory=list)
    denied_capabilities: list[str] = field(default_factory=list)
    required_approvals: list[str] = field(default_factory=list)
    max_cost_usd: float = 10.0
    max_latency_ms: int = 120_000
    data_classes_allowed: list[str] = field(default_factory=lambda: ["public", "internal"])
    audit_level: str = "standard"
    compliance_constraints: list[str] = field(default_factory=list)


class PolicyBundleSelector:
    def select(
        self,
        profile: "GoalRuntimeProfile",
        *,
        tenant_ctx: "TenantContext",
    ) -> CompiledRuntimePolicy:
        from app.orchestration.runtime_profile import RiskLevel

        risk = profile.properties.risk
        compliance = list(profile.security.compliance_tags)
        hitl = profile.security.hitl_required

        audit = "standard"
        if profile.security.audit_level:
            audit = profile.security.audit_level

        max_cost = 10.0
        approvals: list[str] = []

        if risk == RiskLevel.CRITICAL:
            audit = "forensic"
            max_cost = 100.0
            approvals = ["hitl"]
            denied: list[str] = []
        elif risk == RiskLevel.HIGH:
            audit = "full"
            max_cost = 50.0
            approvals = ["hitl"] if hitl else []
            denied = []
        else:
            denied = []

        data_classes = ["public", "internal"]
        if compliance:
            data_classes = ["public", "internal", "confidential"]

        return CompiledRuntimePolicy(
            allowed_capabilities=[],
            denied_capabilities=denied,
            required_approvals=approvals,
            max_cost_usd=max_cost,
            max_latency_ms=120_000,
            data_classes_allowed=data_classes,
            audit_level=audit,
            compliance_constraints=compliance,
        )
```

- [ ] **Step 7.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/security_runtime/ -v --no-cov
```
Expected: `10 passed`

- [ ] **Step 7.7: Commit**

```bash
cd agent-verse-backend
git add app/security_runtime/ tests/security_runtime/
git commit -m "feat(security_runtime): add GuardrailProfileSelector + GovernanceProfileSelector + PolicyBundleSelector"
```

---

## Task 8: DataClassification

**Files:**
- Create: `app/data_classification/__init__.py`
- Create: `app/data_classification/classifier.py`
- Create: `app/data_classification/schema.py`
- Create: `app/data_classification/redaction.py`
- Create: `tests/data_classification/__init__.py`
- Create: `tests/data_classification/test_classifier.py`

- [ ] **Step 8.1: Write failing tests**

```python
# tests/data_classification/test_classifier.py
"""DataClassifier must classify text before it enters prompts or tools."""
from __future__ import annotations
import pytest
from app.data_classification.classifier import DataClassifier
from app.data_classification.schema import DataClass, DataClassification
from app.data_classification.redaction import Redactor


@pytest.fixture
def classifier():
    return DataClassifier()


@pytest.fixture
def redactor():
    return Redactor()


def test_public_text_classified_as_public(classifier):
    result = classifier.classify("The capital of France is Paris.")
    assert DataClass.PUBLIC in result.classes


def test_email_detected_as_pii(classifier):
    result = classifier.classify("Please contact john.doe@example.com for support.")
    assert DataClass.PII in result.classes


def test_credit_card_detected_as_pci(classifier):
    result = classifier.classify("Card number: 4111 1111 1111 1111 expires 12/26")
    assert DataClass.PCI in result.classes


def test_api_key_detected_as_secret(classifier):
    result = classifier.classify("My API key is sk-proj-abc123DEFxyz456")
    assert DataClass.SECRET in result.classes


def test_ssn_detected_as_phi_or_pii(classifier):
    result = classifier.classify("Patient SSN: 123-45-6789")
    assert DataClass.PII in result.classes or DataClass.PHI in result.classes


def test_source_code_detected(classifier):
    result = classifier.classify("def calculate_interest(principal, rate): return principal * rate")
    assert DataClass.SOURCE_CODE in result.classes


def test_internal_data_classification(classifier):
    result = classifier.classify("Internal Q3 revenue forecast: $42M projected growth")
    assert result.highest_sensitivity in (DataClass.INTERNAL, DataClass.CONFIDENTIAL, DataClass.PUBLIC)


def test_redaction_removes_email(redactor):
    text = "Contact alice@company.com for help."
    redacted = redactor.redact(text, classes=[DataClass.PII])
    assert "alice@company.com" not in redacted
    assert "[REDACTED-PII]" in redacted


def test_redaction_removes_credit_card(redactor):
    text = "Card: 4111-1111-1111-1111"
    redacted = redactor.redact(text, classes=[DataClass.PCI])
    assert "4111" not in redacted


def test_classification_result_has_retention_policy(classifier):
    result = classifier.classify("DELETE FROM users WHERE id = 1")
    assert result.retention_policy in ("default", "short", "regulated", "legal_hold")


def test_safe_to_include_in_prompt_blocks_secret(classifier):
    result = classifier.classify("The secret key is AKIA1234567890ABCDEF")
    assert not result.safe_for_prompt


def test_safe_to_include_in_prompt_allows_public(classifier):
    result = classifier.classify("The sky is blue.")
    assert result.safe_for_prompt is True
```

- [ ] **Step 8.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/data_classification
mkdir -p agent-verse-backend/tests/data_classification
touch agent-verse-backend/app/data_classification/__init__.py
touch agent-verse-backend/tests/data_classification/__init__.py
cd agent-verse-backend && uv run pytest tests/data_classification/test_classifier.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 8.3: Implement `app/data_classification/schema.py`**

```python
"""Data classification schema — DataClass enum and DataClassification result."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class DataClass(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
    PII = "pii"
    PHI = "phi"
    PCI = "pci"
    SOURCE_CODE = "source_code"

    @classmethod
    def sensitivity_order(cls) -> list["DataClass"]:
        """Return classes ordered from least to most sensitive."""
        return [cls.PUBLIC, cls.INTERNAL, cls.CONFIDENTIAL, cls.SOURCE_CODE,
                cls.PII, cls.PHI, cls.PCI, cls.SECRET]


@dataclass
class DataClassification:
    data_id: str
    classes: list[DataClass]
    detected_entities: list[str] = field(default_factory=list)
    retention_policy: str = "default"    # default|short|regulated|legal_hold
    allowed_sinks: list[str] = field(default_factory=lambda: ["tenant_user", "audit_log"])
    blocked_sinks: list[str] = field(default_factory=list)

    @property
    def highest_sensitivity(self) -> DataClass:
        order = DataClass.sensitivity_order()
        best = DataClass.PUBLIC
        for cls in self.classes:
            if order.index(cls) > order.index(best):
                best = cls
        return best

    @property
    def safe_for_prompt(self) -> bool:
        """Return True if this data can safely enter an LLM prompt."""
        sensitive = {DataClass.SECRET, DataClass.PHI, DataClass.PCI}
        return not bool(set(self.classes) & sensitive)
```

- [ ] **Step 8.4: Implement `app/data_classification/classifier.py`**

```python
"""DataClassifier — regex-based classification with structured result.

No data enters prompt/context building until classified.
classification-unavailable → safe fallback (assume INTERNAL, block external sinks).
"""
from __future__ import annotations

import re
import uuid
from app.data_classification.schema import DataClass, DataClassification

# Patterns (ordered by specificity)
_PATTERNS: list[tuple[DataClass, re.Pattern[str]]] = [
    # Secrets / credentials
    (DataClass.SECRET, re.compile(
        r"(?i)(sk-proj-[a-zA-Z0-9]+|AKIA[A-Z0-9]{16}|"
        r"ghp_[a-zA-Z0-9]{36}|glpat-[a-zA-Z0-9_-]{20,}|"
        r"xoxb-[0-9]+-[a-zA-Z0-9]+|"
        r"(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,})"
    )),
    # PCI — credit card numbers (Luhn-adjacent patterns)
    (DataClass.PCI, re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|"             # Visa
        r"5[1-5][0-9]{14}|"                            # MC
        r"3[47][0-9]{13}|"                             # Amex
        r"(?:4[0-9]{3}[-\s]?){3}[0-9]{4}|"
        r"(?:5[1-5][0-9]{2}[-\s]?){3}[0-9]{4})\b"
    )),
    # PHI / PII — SSN
    (DataClass.PHI, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # PII — email
    (DataClass.PII, re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # PII — phone numbers
    (DataClass.PII, re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
    # Source code signals
    (DataClass.SOURCE_CODE, re.compile(
        r"(?m)^(?:def |class |import |from .+ import |function |const |let |var |public class )"
    )),
    # Internal signals
    (DataClass.INTERNAL, re.compile(
        r"(?i)\b(internal|confidential|proprietary|q[1-4]\s+revenue|forecast|roadmap)\b"
    )),
]


class DataClassifier:
    """Synchronous regex-based data classifier."""

    def classify(self, text: str, data_id: str | None = None) -> DataClassification:
        found: list[DataClass] = []
        entities: list[str] = []

        for data_class, pattern in _PATTERNS:
            matches = pattern.findall(text)
            if matches:
                found.append(data_class)
                entities.extend(str(m)[:50] for m in matches[:3])

        if not found:
            found = [DataClass.PUBLIC]

        # Retention policy based on highest sensitivity
        from app.data_classification.schema import DataClass as DC
        if DC.SECRET in found or DC.PHI in found or DC.PCI in found:
            retention = "regulated"
            blocked = ["external_tool", "webhook", "logging"]
        elif DC.PII in found:
            retention = "short"
            blocked = ["external_tool"]
        elif DC.INTERNAL in found or DC.CONFIDENTIAL in found:
            retention = "default"
            blocked = []
        else:
            retention = "default"
            blocked = []

        return DataClassification(
            data_id=data_id or uuid.uuid4().hex,
            classes=found,
            detected_entities=entities,
            retention_policy=retention,
            allowed_sinks=["tenant_user", "audit_log"],
            blocked_sinks=blocked,
        )

    def classify_or_safe_fallback(self, text: str) -> DataClassification:
        """Returns classification or INTERNAL fallback if classification fails."""
        try:
            return self.classify(text)
        except Exception:
            return DataClassification(
                data_id=uuid.uuid4().hex,
                classes=[DataClass.INTERNAL],
                retention_policy="default",
                blocked_sinks=["external_tool"],
            )
```

- [ ] **Step 8.5: Implement `app/data_classification/redaction.py`**

```python
"""Redactor — removes sensitive entities from text before logging or external sinks."""
from __future__ import annotations

import re
from app.data_classification.schema import DataClass

_REDACT_PATTERNS: dict[DataClass, list[tuple[re.Pattern[str], str]]] = {
    DataClass.PII: [
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
         "[REDACTED-PII]"),
        (re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
         "[REDACTED-PHONE]"),
    ],
    DataClass.PCI: [
        (re.compile(r"\b(?:4[0-9]{3}[-\s]?){3}[0-9]{4}\b"), "[REDACTED-CARD]"),
        (re.compile(r"\b(?:5[1-5][0-9]{2}[-\s]?){3}[0-9]{4}\b"), "[REDACTED-CARD]"),
    ],
    DataClass.SECRET: [
        (re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}"),
         r"\1=[REDACTED-SECRET]"),
        (re.compile(r"(sk-proj-|AKIA|ghp_|glpat-)[A-Za-z0-9_\-]{8,}"),
         "[REDACTED-KEY]"),
    ],
    DataClass.PHI: [
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    ],
}


class Redactor:
    def redact(self, text: str, classes: list[DataClass] | None = None) -> str:
        if classes is None:
            classes = list(_REDACT_PATTERNS.keys())
        result = text
        for cls in classes:
            for pattern, replacement in _REDACT_PATTERNS.get(cls, []):
                result = pattern.sub(replacement, result)
        return result
```

- [ ] **Step 8.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/data_classification/test_classifier.py -v --no-cov
```
Expected: `12 passed`

- [ ] **Step 8.7: Commit**

```bash
cd agent-verse-backend
git add app/data_classification/ tests/data_classification/
git commit -m "feat(data_classification): add DataClassifier + Redactor — no unclassified data enters prompts"
```

---

## Task 9: CapabilityRegistry

**Files:**
- Create: `app/capabilities/__init__.py`
- Create: `app/capabilities/schema.py`
- Create: `app/capabilities/registry.py`
- Create: `app/capabilities/resolver.py`
- Create: `tests/capabilities/__init__.py`
- Create: `tests/capabilities/test_capability_registry.py`

- [ ] **Step 9.1: Write failing tests**

```python
# tests/capabilities/test_capability_registry.py
"""CapabilityRegistry — orchestration selects by declared capability, not hardcoded name."""
from __future__ import annotations
import pytest
from app.capabilities.registry import CapabilityRegistry, build_default_capability_registry
from app.capabilities.schema import CapabilityProfile, CapabilityKind, RiskLevel as CapRisk
from app.capabilities.resolver import CapabilityResolver


def test_registry_has_core_tools():
    registry = build_default_capability_registry()
    tools = registry.list_by_kind(CapabilityKind.TOOL)
    tool_ids = {c.capability_id for c in tools}
    assert "tool:web_search" in tool_ids
    assert "tool:code_interpreter" in tool_ids
    assert "tool:file_ops" in tool_ids


def test_registry_has_core_models():
    registry = build_default_capability_registry()
    models = registry.list_by_kind(CapabilityKind.MODEL)
    assert len(models) > 0


def test_lookup_by_id():
    registry = build_default_capability_registry()
    cap = registry.get("tool:web_search")
    assert cap is not None
    assert cap.kind == CapabilityKind.TOOL
    assert "text" in cap.input_modalities


def test_nonexistent_returns_none():
    registry = build_default_capability_registry()
    assert registry.get("tool:nonexistent_xyz") is None


def test_filter_by_risk():
    registry = build_default_capability_registry()
    low_risk = registry.filter(max_risk=CapRisk.LOW)
    assert all(c.risk_level == CapRisk.LOW for c in low_risk)


def test_resolver_selects_by_modality():
    registry = build_default_capability_registry()
    resolver = CapabilityResolver(registry)
    # Select a tool that can handle web input
    results = resolver.find_tools_for_modalities(["text"], output_modalities=["text"])
    assert len(results) > 0


def test_capability_profile_has_required_fields():
    cap = CapabilityProfile(
        capability_id="tool:test",
        kind=CapabilityKind.TOOL,
        tenant_scope="platform",
        input_modalities=["text"],
        output_modalities=["text"],
        risk_level=CapRisk.LOW,
        cost_class="free",
        latency_class="realtime",
        reliability_score=0.99,
        required_permissions=[],
    )
    assert cap.capability_id == "tool:test"
    assert cap.reliability_score == 0.99
```

- [ ] **Step 9.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/capabilities
mkdir -p agent-verse-backend/tests/capabilities
touch agent-verse-backend/app/capabilities/__init__.py
touch agent-verse-backend/tests/capabilities/__init__.py
cd agent-verse-backend && uv run pytest tests/capabilities/test_capability_registry.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 9.3: Implement `app/capabilities/schema.py`**

```python
"""CapabilityProfile schema — normalises what every capability can do."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class CapabilityKind(str, enum.Enum):
    TOOL = "tool"
    MODEL = "model"
    AGENT = "agent"
    SKILL = "skill"
    RETRIEVER = "retriever"
    EMBEDDER = "embedder"
    PARSER = "parser"
    CHUNKER = "chunker"
    GUARDRAIL = "guardrail"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CapabilityProfile:
    capability_id: str
    kind: CapabilityKind
    tenant_scope: str                              # platform|tenant|agent
    input_modalities: list[str]
    output_modalities: list[str]
    risk_level: RiskLevel
    cost_class: str                                # free|low|medium|high
    latency_class: str                             # realtime|interactive|batch
    reliability_score: float                       # 0.0–1.0
    required_permissions: list[str]
    description: str = ""
    tags: list[str] = field(default_factory=list)
```

- [ ] **Step 9.4: Implement `app/capabilities/registry.py`**

```python
"""CapabilityRegistry — platform-wide catalogue of all capabilities."""
from __future__ import annotations

from app.capabilities.schema import CapabilityKind, CapabilityProfile, RiskLevel


class CapabilityRegistry:
    def __init__(self, entries: list[CapabilityProfile]) -> None:
        self._by_id: dict[str, CapabilityProfile] = {e.capability_id: e for e in entries}

    def get(self, capability_id: str) -> CapabilityProfile | None:
        return self._by_id.get(capability_id)

    def list_by_kind(self, kind: CapabilityKind) -> list[CapabilityProfile]:
        return [c for c in self._by_id.values() if c.kind == kind]

    def filter(
        self,
        *,
        kind: CapabilityKind | None = None,
        max_risk: RiskLevel | None = None,
        required_modality: str | None = None,
    ) -> list[CapabilityProfile]:
        order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        results = list(self._by_id.values())
        if kind:
            results = [c for c in results if c.kind == kind]
        if max_risk:
            max_idx = order.index(max_risk)
            results = [c for c in results if order.index(c.risk_level) <= max_idx]
        if required_modality:
            results = [c for c in results if required_modality in c.input_modalities]
        return results

    def list_all(self) -> list[CapabilityProfile]:
        return list(self._by_id.values())


def build_default_capability_registry() -> CapabilityRegistry:
    T = CapabilityKind.TOOL
    M = CapabilityKind.MODEL
    R = CapabilityKind.RETRIEVER
    E = CapabilityKind.EMBEDDER
    G = CapabilityKind.GUARDRAIL

    entries = [
        # Tools
        CapabilityProfile("tool:web_search", T, "platform", ["text"], ["text", "json"],
            RiskLevel.LOW, "low", "interactive", 0.95, [], "Web search via SearxNG/DDG"),
        CapabilityProfile("tool:code_interpreter", T, "platform", ["text", "code"], ["text", "json"],
            RiskLevel.MEDIUM, "medium", "interactive", 0.90, ["code_execution"],
            "Python sandbox code execution"),
        CapabilityProfile("tool:file_ops", T, "platform", ["text"], ["text", "json"],
            RiskLevel.MEDIUM, "low", "interactive", 0.98, [], "File read/write operations"),
        CapabilityProfile("tool:shell", T, "platform", ["text"], ["text"],
            RiskLevel.HIGH, "low", "interactive", 0.88, ["shell_access"], "Shell command execution"),
        CapabilityProfile("tool:http", T, "platform", ["text", "json"], ["text", "json"],
            RiskLevel.MEDIUM, "low", "realtime", 0.92, [], "HTTP requests to external APIs"),
        CapabilityProfile("tool:document_parser", T, "platform", ["text", "pdf", "docx"], ["text"],
            RiskLevel.LOW, "low", "interactive", 0.97, [], "Parse PDF/DOCX/HTML documents"),
        CapabilityProfile("tool:artifact", T, "platform", ["text"], ["text", "json"],
            RiskLevel.LOW, "free", "realtime", 0.99, [], "Store/retrieve result artifacts"),
        # Models (abstract — resolved to real provider at runtime)
        CapabilityProfile("model:text_generation", M, "platform", ["text"], ["text"],
            RiskLevel.LOW, "medium", "interactive", 0.99, [], "General text generation model"),
        CapabilityProfile("model:vision", M, "platform", ["text", "image"], ["text"],
            RiskLevel.LOW, "high", "interactive", 0.97, [], "Vision + text model"),
        CapabilityProfile("model:code", M, "platform", ["text", "code"], ["text", "code"],
            RiskLevel.LOW, "medium", "interactive", 0.98, [], "Code generation model"),
        CapabilityProfile("model:embedding", M, "platform", ["text"], ["vector"],
            RiskLevel.LOW, "low", "realtime", 0.999, [], "Text embedding model"),
        # Retrievers
        CapabilityProfile("retriever:vector", R, "tenant", ["text", "vector"], ["text"],
            RiskLevel.LOW, "free", "realtime", 0.97, [], "pgvector similarity search"),
        CapabilityProfile("retriever:web", R, "platform", ["text"], ["text"],
            RiskLevel.LOW, "low", "interactive", 0.90, [], "Live web retrieval"),
        CapabilityProfile("retriever:graph", R, "tenant", ["text"], ["text", "json"],
            RiskLevel.LOW, "low", "interactive", 0.88, [], "Knowledge graph traversal"),
        # Embedders
        CapabilityProfile("embedder:text", E, "platform", ["text"], ["vector"],
            RiskLevel.LOW, "low", "realtime", 0.999, [], "Text embedding"),
        CapabilityProfile("embedder:code", E, "platform", ["code", "text"], ["vector"],
            RiskLevel.LOW, "low", "realtime", 0.99, [], "Code embedding"),
        CapabilityProfile("embedder:multimodal", E, "platform", ["text", "image"], ["vector"],
            RiskLevel.LOW, "medium", "interactive", 0.95, [], "Multimodal embedding"),
        # Guardrails
        CapabilityProfile("guardrail:injection", G, "platform", ["text"], ["bool"],
            RiskLevel.LOW, "free", "realtime", 0.99, [], "Prompt injection scanner"),
        CapabilityProfile("guardrail:pii", G, "platform", ["text"], ["bool", "text"],
            RiskLevel.LOW, "free", "realtime", 0.97, [], "PII detection + redaction"),
        CapabilityProfile("guardrail:toxicity", G, "platform", ["text"], ["bool"],
            RiskLevel.LOW, "free", "realtime", 0.99, [], "Toxicity scorer"),
    ]
    return CapabilityRegistry(entries)
```

- [ ] **Step 9.5: Implement `app/capabilities/resolver.py`**

```python
"""CapabilityResolver — finds capabilities matching modality/risk/cost constraints."""
from __future__ import annotations

from app.capabilities.registry import CapabilityRegistry
from app.capabilities.schema import CapabilityKind, CapabilityProfile, RiskLevel


class CapabilityResolver:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def find_tools_for_modalities(
        self,
        input_modalities: list[str],
        *,
        output_modalities: list[str] | None = None,
        max_risk: RiskLevel = RiskLevel.HIGH,
    ) -> list[CapabilityProfile]:
        tools = self._registry.filter(kind=CapabilityKind.TOOL, max_risk=max_risk)
        results = [
            t for t in tools
            if any(m in t.input_modalities for m in input_modalities)
        ]
        if output_modalities:
            results = [
                t for t in results
                if any(m in t.output_modalities for m in output_modalities)
            ]
        return results
```

- [ ] **Step 9.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/capabilities/test_capability_registry.py -v --no-cov
```
Expected: `8 passed`

- [ ] **Step 9.7: Commit**

```bash
cd agent-verse-backend
git add app/capabilities/ tests/capabilities/
git commit -m "feat(capabilities): add CapabilityRegistry — orchestration selects by declared capability"
```

---

## Task 10: PlanRuntime Verifier

**Files:**
- Create: `app/plan_runtime/__init__.py`
- Create: `app/plan_runtime/plan_verifier.py`
- Create: `app/plan_runtime/plan_risk_analyzer.py`
- Create: `app/plan_runtime/plan_cost_estimator.py`
- Create: `tests/plan_runtime/__init__.py`
- Create: `tests/plan_runtime/test_plan_verifier.py`

- [ ] **Step 10.1: Write failing tests**

```python
# tests/plan_runtime/test_plan_verifier.py
"""No high-risk plan executes without PlanVerificationResult recorded."""
from __future__ import annotations
import pytest
from app.plan_runtime.plan_verifier import PlanVerifier, PlanVerificationResult
from app.plan_runtime.plan_risk_analyzer import PlanRiskAnalyzer
from app.plan_runtime.plan_cost_estimator import PlanCostEstimator
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
)


def _make_profile(risk: RiskLevel = RiskLevel.LOW) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=risk),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_safe_plan_passes_verification():
    verifier = PlanVerifier()
    plan = ["Search for open Jira tickets", "Summarize results"]
    profile = _make_profile(RiskLevel.LOW)
    result = verifier.verify(plan=plan, profile=profile)
    assert result.feasible is True
    assert result.safe_to_execute is True
    assert result.risk_level in ("low", "medium")


def test_destructive_plan_requires_hitl():
    verifier = PlanVerifier()
    plan = ["Delete all records from users table", "Drop the production database"]
    profile = _make_profile(RiskLevel.CRITICAL)
    result = verifier.verify(plan=plan, profile=profile)
    assert result.requires_hitl is True
    assert result.risk_level in ("high", "critical")


def test_empty_plan_is_not_feasible():
    verifier = PlanVerifier()
    result = verifier.verify(plan=[], profile=_make_profile())
    assert result.feasible is False


def test_too_many_steps_flags_warning():
    verifier = PlanVerifier()
    plan = [f"Step {i}: do something" for i in range(60)]
    result = verifier.verify(plan=plan, profile=_make_profile())
    assert len(result.warnings) > 0


def test_risk_analyzer_flags_destructive_steps():
    analyzer = PlanRiskAnalyzer()
    plan = ["List all users", "Delete user account permanently"]
    risk_level, findings = analyzer.analyze(plan)
    assert risk_level in ("high", "critical")
    assert any("delete" in f.lower() or "destructive" in f.lower() for f in findings)


def test_cost_estimator_returns_estimate():
    estimator = PlanCostEstimator()
    plan = ["Search for docs", "Summarize findings", "Write report"]
    estimate = estimator.estimate(plan, model_cost_class="medium")
    assert estimate.estimated_cost_usd >= 0.0
    assert estimate.estimated_tokens > 0


def test_verification_result_serializable():
    import json
    verifier = PlanVerifier()
    plan = ["Deploy to production"]
    result = verifier.verify(plan=plan, profile=_make_profile(RiskLevel.CRITICAL))
    data = result.to_dict()
    json.dumps(data)  # must not raise
```

- [ ] **Step 10.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/plan_runtime
mkdir -p agent-verse-backend/tests/plan_runtime
touch agent-verse-backend/app/plan_runtime/__init__.py
touch agent-verse-backend/tests/plan_runtime/__init__.py
cd agent-verse-backend && uv run pytest tests/plan_runtime/test_plan_verifier.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 10.3: Implement `app/plan_runtime/plan_risk_analyzer.py`**

```python
"""PlanRiskAnalyzer — scans plan steps for high-risk actions."""
from __future__ import annotations

import re

_CRITICAL_PATTERNS = [
    (re.compile(r"\b(delete|drop|truncate|wipe|purge|destroy)\b", re.I), "destructive operation"),
    (re.compile(r"\b(production|prod)\b.*\b(deploy|delete|migrate|drop)\b", re.I), "production mutation"),
    (re.compile(r"\bcharge\b|\btransfer funds\b|\bpayment\b", re.I), "financial operation"),
]

_HIGH_PATTERNS = [
    (re.compile(r"\b(deploy|migrate|alter|publish|release)\b", re.I), "state-changing operation"),
    (re.compile(r"\b(send email|send sms|notify all|broadcast)\b", re.I), "external notification"),
    (re.compile(r"\b(grant admin|revoke access|disable account)\b", re.I), "access control change"),
]


class PlanRiskAnalyzer:
    def analyze(self, plan: list[str]) -> tuple[str, list[str]]:
        """Returns (risk_level, list_of_findings)."""
        findings: list[str] = []
        max_risk = "low"

        for step in plan:
            for pattern, label in _CRITICAL_PATTERNS:
                if pattern.search(step):
                    findings.append(f"CRITICAL: {label} in step: {step[:80]}")
                    max_risk = "critical"

            if max_risk != "critical":
                for pattern, label in _HIGH_PATTERNS:
                    if pattern.search(step):
                        findings.append(f"HIGH: {label} in step: {step[:80]}")
                        if max_risk not in ("critical", "high"):
                            max_risk = "high"

        return max_risk, findings
```

- [ ] **Step 10.4: Implement `app/plan_runtime/plan_cost_estimator.py`**

```python
"""PlanCostEstimator — rough cost estimate before plan execution."""
from __future__ import annotations

from dataclasses import dataclass

_TOKENS_PER_STEP = {
    "low": 800,
    "medium": 1500,
    "high": 3000,
}
_COST_PER_1K_TOKENS = {
    "low": 0.0003,
    "medium": 0.003,
    "high": 0.015,
}


@dataclass
class CostEstimate:
    estimated_cost_usd: float
    estimated_tokens: int
    steps: int
    cost_class: str


class PlanCostEstimator:
    def estimate(self, plan: list[str], model_cost_class: str = "medium") -> CostEstimate:
        tokens_per = _TOKENS_PER_STEP.get(model_cost_class, 1500)
        cost_per_k = _COST_PER_1K_TOKENS.get(model_cost_class, 0.003)
        total_tokens = len(plan) * tokens_per
        total_cost = (total_tokens / 1000) * cost_per_k
        return CostEstimate(
            estimated_cost_usd=round(total_cost, 6),
            estimated_tokens=total_tokens,
            steps=len(plan),
            cost_class=model_cost_class,
        )
```

- [ ] **Step 10.5: Implement `app/plan_runtime/plan_verifier.py`**

```python
"""PlanVerifier — verifies plan safety, feasibility, cost before execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from app.plan_runtime.plan_risk_analyzer import PlanRiskAnalyzer
from app.plan_runtime.plan_cost_estimator import PlanCostEstimator

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class PlanVerificationResult:
    feasible: bool
    risk_level: str
    estimated_cost_usd: float
    requires_hitl: bool
    safe_to_execute: bool
    missing_permissions: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "risk_level": self.risk_level,
            "estimated_cost_usd": self.estimated_cost_usd,
            "requires_hitl": self.requires_hitl,
            "safe_to_execute": self.safe_to_execute,
            "missing_permissions": self.missing_permissions,
            "missing_context": self.missing_context,
            "warnings": self.warnings,
            "findings": self.findings,
        }


class PlanVerifier:
    def __init__(self) -> None:
        self._risk_analyzer = PlanRiskAnalyzer()
        self._cost_estimator = PlanCostEstimator()

    def verify(
        self,
        *,
        plan: list[str],
        profile: "GoalRuntimeProfile",
    ) -> PlanVerificationResult:
        warnings: list[str] = []

        # Feasibility checks
        if not plan:
            return PlanVerificationResult(
                feasible=False, risk_level="unknown",
                estimated_cost_usd=0.0, requires_hitl=False, safe_to_execute=False,
                findings=["Empty plan — cannot execute"],
            )

        if len(plan) > 50:
            warnings.append(f"Plan has {len(plan)} steps — consider breaking into sub-goals")

        # Risk analysis
        risk_level, findings = self._risk_analyzer.analyze(plan)

        # Cost estimate
        cost_est = self._cost_estimator.estimate(
            plan, model_cost_class=profile.model_plan.cost_class
        )

        # HITL requirement
        hitl_required = (
            profile.security.hitl_required
            or risk_level in ("high", "critical")
        )

        safe = risk_level not in ("critical",) or hitl_required

        return PlanVerificationResult(
            feasible=True,
            risk_level=risk_level,
            estimated_cost_usd=cost_est.estimated_cost_usd,
            requires_hitl=hitl_required,
            safe_to_execute=safe,
            warnings=warnings,
            findings=findings,
        )
```

- [ ] **Step 10.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/plan_runtime/test_plan_verifier.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step 10.7: Commit**

```bash
cd agent-verse-backend
git add app/plan_runtime/ tests/plan_runtime/
git commit -m "feat(plan_runtime): add PlanVerifier + PlanRiskAnalyzer + PlanCostEstimator"
```

---

## Task 11: RuntimeReadiness Gate

**Files:**
- Create: `app/runtime_readiness/__init__.py`
- Create: `app/runtime_readiness/readiness_gate.py`
- Create: `app/runtime_readiness/dependency_health.py`
- Create: `app/runtime_readiness/degraded_mode_policy.py`
- Create: `tests/runtime_readiness/__init__.py`
- Create: `tests/runtime_readiness/test_readiness_gate.py`

- [ ] **Step 11.1: Write failing tests**

```python
# tests/runtime_readiness/test_readiness_gate.py
"""ReadinessGate blocks or degrades goals when critical deps are unavailable."""
from __future__ import annotations
import pytest
from app.runtime_readiness.readiness_gate import ReadinessGate, ReadinessResult
from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
from app.runtime_readiness.degraded_mode_policy import DegradedModePolicy
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
)


def _make_profile(risk: RiskLevel = RiskLevel.LOW) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=risk),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(sources=["knowledge_base"]),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_all_healthy_returns_ready():
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY,
        redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY,
        llm_provider=DepStatus.HEALTHY,
    )
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is True
    assert result.degraded is False


def test_postgres_down_blocks_goal():
    health = DependencyHealth(
        postgres=DepStatus.UNAVAILABLE,
        redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY,
        llm_provider=DepStatus.HEALTHY,
    )
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is False
    assert "postgres" in result.blocking_deps


def test_embedder_down_degrades_rag():
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY,
        redis=DepStatus.HEALTHY,
        embedder=DepStatus.UNAVAILABLE,
        llm_provider=DepStatus.HEALTHY,
    )
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    # Can still run with degraded RAG (lexical only)
    assert result.degraded is True
    assert any("embedder" in w.lower() for w in result.degradation_warnings)


def test_llm_down_blocks_goal():
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY,
        redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY,
        llm_provider=DepStatus.UNAVAILABLE,
    )
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is False
    assert "llm_provider" in result.blocking_deps


def test_degraded_mode_policy_disables_vector_search():
    policy = DegradedModePolicy()
    profile = _make_profile()
    updated = policy.apply_degraded_rag(profile, unavailable_deps={"embedder"})
    # Should fall back to lexical-only
    assert updated.rag_strategy.embedding_model in ("none", "lexical", "default")


def test_readiness_result_serializable():
    import json
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY,
        redis=DepStatus.DEGRADED,
        embedder=DepStatus.HEALTHY,
        llm_provider=DepStatus.HEALTHY,
    )
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    json.dumps(result.to_dict())
```

- [ ] **Step 11.2: Create dirs and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/runtime_readiness
mkdir -p agent-verse-backend/tests/runtime_readiness
touch agent-verse-backend/app/runtime_readiness/__init__.py
touch agent-verse-backend/tests/runtime_readiness/__init__.py
cd agent-verse-backend && uv run pytest tests/runtime_readiness/test_readiness_gate.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step 11.3: Implement `app/runtime_readiness/dependency_health.py`**

```python
"""DependencyHealth — snapshot of all critical service health states."""
from __future__ import annotations

import enum
from dataclasses import dataclass


class DepStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class DependencyHealth:
    postgres: DepStatus = DepStatus.UNKNOWN
    redis: DepStatus = DepStatus.UNKNOWN
    embedder: DepStatus = DepStatus.UNKNOWN
    llm_provider: DepStatus = DepStatus.UNKNOWN
    celery: DepStatus = DepStatus.UNKNOWN
    web_search: DepStatus = DepStatus.UNKNOWN
    kg_store: DepStatus = DepStatus.UNKNOWN

    @classmethod
    def all_healthy(cls) -> "DependencyHealth":
        return cls(
            postgres=DepStatus.HEALTHY,
            redis=DepStatus.HEALTHY,
            embedder=DepStatus.HEALTHY,
            llm_provider=DepStatus.HEALTHY,
            celery=DepStatus.HEALTHY,
            web_search=DepStatus.HEALTHY,
            kg_store=DepStatus.HEALTHY,
        )
```

- [ ] **Step 11.4: Implement `app/runtime_readiness/degraded_mode_policy.py`**

```python
"""DegradedModePolicy — adapts GoalRuntimeProfile when deps are unavailable."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class DegradedModePolicy:
    def apply_degraded_rag(
        self,
        profile: "GoalRuntimeProfile",
        unavailable_deps: set[str],
    ) -> "GoalRuntimeProfile":
        rag = profile.rag_strategy
        if "embedder" in unavailable_deps:
            rag = dataclasses.replace(rag, embedding_model="lexical", strategy="naive_rag")
        if "kg_store" in unavailable_deps:
            rag = dataclasses.replace(rag, graph_strategy="none")
        if "web_search" in unavailable_deps:
            rag = dataclasses.replace(rag, web_fallback_enabled=False)
        return dataclasses.replace(profile, rag_strategy=rag)
```

- [ ] **Step 11.5: Implement `app/runtime_readiness/readiness_gate.py`**

```python
"""ReadinessGate — blocks or degrades goals when critical deps are unavailable."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile

# These deps are mandatory — goal cannot run without them
_BLOCKING_DEPS = {"postgres", "llm_provider"}
# These deps allow degraded execution
_DEGRADABLE_DEPS = {"redis", "embedder", "celery", "web_search", "kg_store"}


@dataclass
class ReadinessResult:
    ready: bool
    degraded: bool = False
    blocking_deps: list[str] = field(default_factory=list)
    degradation_warnings: list[str] = field(default_factory=list)
    unavailable_optional: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "degraded": self.degraded,
            "blocking_deps": self.blocking_deps,
            "degradation_warnings": self.degradation_warnings,
            "unavailable_optional": self.unavailable_optional,
        }


class ReadinessGate:
    def __init__(self, health: DependencyHealth) -> None:
        self._health = health

    def check(self, profile: "GoalRuntimeProfile") -> ReadinessResult:
        blocking: list[str] = []
        warnings: list[str] = []
        optional_down: list[str] = []

        health_map = {
            "postgres": self._health.postgres,
            "redis": self._health.redis,
            "embedder": self._health.embedder,
            "llm_provider": self._health.llm_provider,
            "celery": self._health.celery,
            "web_search": self._health.web_search,
            "kg_store": self._health.kg_store,
        }

        for dep, status in health_map.items():
            if status == DepStatus.UNAVAILABLE:
                if dep in _BLOCKING_DEPS:
                    blocking.append(dep)
                else:
                    optional_down.append(dep)
                    warnings.append(
                        f"{dep} unavailable — functionality will be degraded"
                    )
            elif status == DepStatus.DEGRADED:
                warnings.append(f"{dep} is degraded — performance may be affected")

        ready = len(blocking) == 0
        degraded = len(optional_down) > 0 or len(warnings) > 0

        return ReadinessResult(
            ready=ready,
            degraded=degraded,
            blocking_deps=blocking,
            degradation_warnings=warnings,
            unavailable_optional=optional_down,
        )
```

- [ ] **Step 11.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/runtime_readiness/test_readiness_gate.py -v --no-cov
```
Expected: `6 passed`

- [ ] **Step 11.7: Run all P0 tests together**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/ tests/security_runtime/ tests/data_classification/ \
    tests/capabilities/ tests/plan_runtime/ tests/runtime_readiness/ -v --no-cov
```
Expected: All 60+ tests pass

- [ ] **Step 11.8: Commit**

```bash
cd agent-verse-backend
git add app/runtime_readiness/ tests/runtime_readiness/
git commit -m "feat(runtime_readiness): add ReadinessGate — blocks unsafe degraded execution"
```

---

## Task 12: PolicyRuntime Compiler

**Files:**
- Create: `app/policy_runtime/__init__.py`
- Create: `app/policy_runtime/compiler.py`
- Create: `app/policy_runtime/constraint_model.py`
- Create: `app/policy_runtime/runtime_enforcer.py`
- Create: `tests/policy_runtime/__init__.py`
- Create: `tests/policy_runtime/test_policy_compiler.py`

- [ ] **Step 12.1: Write failing tests**

```python
# tests/policy_runtime/test_policy_compiler.py
"""PolicyCompiler runs BEFORE pattern/model/tool selection — gates everything."""
from __future__ import annotations
import pytest
from app.policy_runtime.compiler import PolicyCompiler
from app.policy_runtime.constraint_model import RuntimeConstraints
from app.policy_runtime.runtime_enforcer import RuntimeEnforcer
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
)
from app.tenancy.context import TenantContext, PlanTier


def _make_profile(risk: RiskLevel = RiskLevel.LOW) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=risk),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def _make_tenant(plan: PlanTier = PlanTier.PROFESSIONAL) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=plan, api_key_id="k1")


def test_compile_returns_constraints():
    compiler = PolicyCompiler()
    constraints = compiler.compile(_make_profile(), tenant_ctx=_make_tenant())
    assert isinstance(constraints, RuntimeConstraints)
    assert constraints.max_cost_usd > 0
    assert constraints.audit_level in ("standard", "full", "forensic")


def test_critical_risk_forces_hitl_constraint():
    compiler = PolicyCompiler()
    profile = _make_profile(RiskLevel.CRITICAL)
    constraints = compiler.compile(profile, tenant_ctx=_make_tenant())
    assert "hitl" in constraints.required_approvals


def test_free_plan_has_lower_cost_limit():
    compiler = PolicyCompiler()
    free = compiler.compile(_make_profile(), tenant_ctx=_make_tenant(PlanTier.FREE))
    pro = compiler.compile(_make_profile(), tenant_ctx=_make_tenant(PlanTier.PROFESSIONAL))
    assert free.max_cost_usd <= pro.max_cost_usd


def test_enforcer_allows_tool_in_constraints():
    enforcer = RuntimeEnforcer()
    constraints = RuntimeConstraints(
        allowed_capabilities=["tool:web_search"],
        denied_capabilities=[],
        required_approvals=[],
        max_cost_usd=10.0,
        audit_level="standard",
    )
    assert enforcer.is_capability_allowed("tool:web_search", constraints) is True


def test_enforcer_blocks_denied_tool():
    enforcer = RuntimeEnforcer()
    constraints = RuntimeConstraints(
        allowed_capabilities=[],
        denied_capabilities=["tool:shell"],
        required_approvals=[],
        max_cost_usd=10.0,
        audit_level="standard",
    )
    assert enforcer.is_capability_allowed("tool:shell", constraints) is False


def test_enforcer_empty_allowed_means_all_allowed():
    enforcer = RuntimeEnforcer()
    constraints = RuntimeConstraints(
        allowed_capabilities=[],    # empty = all allowed
        denied_capabilities=[],
        required_approvals=[],
        max_cost_usd=10.0,
        audit_level="standard",
    )
    assert enforcer.is_capability_allowed("tool:web_search", constraints) is True
```

- [ ] **Step 12.2: Create dirs, implement, and run**

```bash
mkdir -p agent-verse-backend/app/policy_runtime
mkdir -p agent-verse-backend/tests/policy_runtime
touch agent-verse-backend/app/policy_runtime/__init__.py
touch agent-verse-backend/tests/policy_runtime/__init__.py
```

Implement `app/policy_runtime/constraint_model.py`:

```python
"""RuntimeConstraints — compiled policy constraints fed to every downstream selector."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RuntimeConstraints:
    allowed_capabilities: list[str]  # empty = all allowed
    denied_capabilities: list[str]
    required_approvals: list[str]
    max_cost_usd: float
    audit_level: str
    data_classes_allowed: list[str] = field(default_factory=lambda: ["public", "internal"])
    compliance_constraints: list[str] = field(default_factory=list)
    max_latency_ms: int = 120_000
```

Implement `app/policy_runtime/compiler.py`:

```python
"""PolicyCompiler — compiles tenant + risk + compliance policy into RuntimeConstraints."""
from __future__ import annotations
from typing import TYPE_CHECKING
from app.policy_runtime.constraint_model import RuntimeConstraints

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


class PolicyCompiler:
    def compile(
        self,
        profile: "GoalRuntimeProfile",
        *,
        tenant_ctx: "TenantContext",
    ) -> RuntimeConstraints:
        from app.orchestration.runtime_profile import RiskLevel
        from app.tenancy.context import PlanTier

        risk = profile.properties.risk
        plan = tenant_ctx.plan
        compliance = list(profile.security.compliance_tags)

        # Cost limits by plan
        cost_by_plan = {
            PlanTier.FREE: 2.0,
            PlanTier.STARTER: 10.0,
            PlanTier.PROFESSIONAL: 50.0,
            PlanTier.ENTERPRISE: 500.0,
        }
        max_cost = cost_by_plan.get(plan, 10.0)

        # Audit level
        audit = "standard"
        if risk == RiskLevel.CRITICAL:
            audit = "forensic"
        elif risk == RiskLevel.HIGH:
            audit = "full"
        elif compliance:
            audit = "full"

        # Required approvals
        approvals: list[str] = []
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            approvals = ["hitl"]

        # Denied capabilities — none by default (deny-list approach)
        denied: list[str] = []
        if plan == PlanTier.FREE:
            denied = ["tool:shell"]  # shell not available on free plan

        data_classes = ["public", "internal"]
        if compliance:
            data_classes = ["public", "internal", "confidential"]

        return RuntimeConstraints(
            allowed_capabilities=[],   # empty = all allowed (minus denied)
            denied_capabilities=denied,
            required_approvals=approvals,
            max_cost_usd=max_cost,
            audit_level=audit,
            data_classes_allowed=data_classes,
            compliance_constraints=compliance,
        )
```

Implement `app/policy_runtime/runtime_enforcer.py`:

```python
"""RuntimeEnforcer — checks capability/action against compiled constraints."""
from __future__ import annotations
from app.policy_runtime.constraint_model import RuntimeConstraints


class RuntimeEnforcer:
    def is_capability_allowed(self, capability_id: str, constraints: RuntimeConstraints) -> bool:
        if capability_id in constraints.denied_capabilities:
            return False
        if constraints.allowed_capabilities:
            return capability_id in constraints.allowed_capabilities
        return True   # empty allowed_capabilities = all allowed

    def requires_approval(self, constraints: RuntimeConstraints) -> bool:
        return bool(constraints.required_approvals)

    def check_cost(self, estimated_cost_usd: float, constraints: RuntimeConstraints) -> bool:
        return estimated_cost_usd <= constraints.max_cost_usd
```

- [ ] **Step 12.3: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/policy_runtime/test_policy_compiler.py -v --no-cov
```
Expected: `6 passed`

- [ ] **Step 12.4: Run complete P0 test suite**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/ tests/security_runtime/ tests/data_classification/ \
    tests/capabilities/ tests/plan_runtime/ tests/runtime_readiness/ \
    tests/policy_runtime/ -v --no-cov
```
Expected: All 70+ tests pass

- [ ] **Step 12.5: Commit**

```bash
cd agent-verse-backend
git add app/policy_runtime/ tests/policy_runtime/
git commit -m "feat(policy_runtime): add PolicyCompiler + RuntimeEnforcer — P0 security gates complete"
```
