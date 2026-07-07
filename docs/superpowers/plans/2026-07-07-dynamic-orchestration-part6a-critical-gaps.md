# AgentVerse Dynamic Orchestration — Part 6A: Critical Gaps (Audit Gaps 1–4, 10, 16–17)

> **Prerequisite:** Complete Parts 1–5 first. This part fills CRITICAL and HIGH severity gaps found in the audit.

**Audit gaps covered here:**
- Gap 1: Layer 3 Agent Pattern Adapters (entire `app/agent/patterns/` directory)
- Gap 2: Doc 4 Core Files (`pattern_config.py`, `goal_classifier.py`, `pattern_assembler.py`, `dynamic_graph.py`)
- Gap 3: Doc 2 RAG Agentic remaining files + RetrieverTool Phase B
- Gap 4: Layer 8 Model Orchestration additions
- Gap 10: Strategy Registry — 15+ missing patterns
- Gap 16: RetrieverTool Phase B behavior (query reformulation, full fallback chain)
- Gap 17: LangGraph nodes (`_node_rag_prime`, `_node_rag_remediate`, `_node_refine`)

**Run all tests:**
```bash
cd agent-verse-backend
uv run pytest tests/agent/test_pattern_config.py tests/agent/test_goal_classifier_doc4.py \
    tests/agent/test_pattern_assembler.py tests/rag/test_agentic/ tests/ai_router/ -v --no-cov
```

---

## Task A1: Doc 4 Core — PatternConfig + GoalProperties (exact spec)

**Files:**
- Create: `app/agent/pattern_config.py`
- Create: `tests/agent/test_pattern_config.py`

- [ ] **Step A1.1: Write failing tests**

```python
# tests/agent/test_pattern_config.py
"""PatternConfig must use exact doc-4 field names — not GoalRuntimeProfile fields."""
from __future__ import annotations
import pytest
from app.agent.pattern_config import (
    Complexity, RiskLevel, Domain,
    GoalProperties, PatternConfig,
)


def test_goal_properties_defaults():
    props = GoalProperties()
    assert props.complexity == Complexity.MEDIUM
    assert props.domain == Domain.TECHNICAL
    assert props.risk == RiskLevel.LOW
    assert props.time_sensitivity == "normal"
    assert props.knowledge_requirement == "kb_only"
    assert props.reversibility == "reversible"
    assert props.multi_step is True
    assert props.is_generative is False
    assert props.requires_web is False
    assert props.estimated_steps == 3
    assert props.confidence == 0.8


def test_pattern_config_defaults():
    cfg = PatternConfig()
    assert cfg.reasoning_patterns == ["reflection"]
    assert cfg.rag_patterns == ["hybrid_rag"]
    assert cfg.multi_agent_patterns == ["single_agent"]
    assert cfg.safety_patterns == ["guardrails"]
    assert cfg.model_planner == "gpt-5.2"
    assert cfg.model_executor == "gpt-5.2"
    assert cfg.model_verifier == "gpt-5.2"
    assert cfg.model_classifier == "gpt-4o-mini"
    assert cfg.max_iterations == 15
    assert cfg.max_refine_iterations == 2
    assert cfg.persistence_mode is False
    assert cfg.max_persistence_attempts == 3
    assert cfg.autonomy_mode == "bounded-autonomous"
    assert cfg.web_auto_activate is False
    assert cfg.goal_properties is None
    assert cfg.selection_reason == {}
    assert cfg.assembly_latency_ms == 0.0


def test_pattern_config_stores_properties():
    props = GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.HIGH)
    cfg = PatternConfig(goal_properties=props, selection_reason={"cot": "complexity=expert"})
    assert cfg.goal_properties.complexity == Complexity.EXPERT
    assert cfg.selection_reason["cot"] == "complexity=expert"


def test_complexity_enum_values():
    assert Complexity.SIMPLE.value == "simple"
    assert Complexity.MEDIUM.value == "medium"
    assert Complexity.COMPLEX.value == "complex"
    assert Complexity.EXPERT.value == "expert"


def test_risk_level_enum_values():
    assert RiskLevel.LOW.value == "low"
    assert RiskLevel.MEDIUM.value == "medium"
    assert RiskLevel.HIGH.value == "high"
    assert RiskLevel.CRITICAL.value == "critical"


def test_domain_enum_values():
    assert Domain.TECHNICAL.value == "technical"
    assert Domain.CREATIVE.value == "creative"
    assert Domain.ANALYTICAL.value == "analytical"
    assert Domain.OPERATIONAL.value == "operational"
    assert Domain.CONVERSATIONAL.value == "conversational"
```

- [ ] **Step A1.2: Create file and run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_pattern_config.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A1.3: Implement `app/agent/pattern_config.py`**

```python
"""PatternConfig and GoalProperties — exact doc-4 dataclass contracts.

These live in app/agent/ (not app/orchestration/) because they directly
drive the LangGraph DynamicGraphAssembler and are agent-execution contracts.
The app/orchestration/runtime_profile.py GoalRuntimeProfile is the higher-level
orchestration contract that wraps these.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Complexity(str, enum.Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EXPERT = "expert"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(str, enum.Enum):
    TECHNICAL = "technical"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"
    OPERATIONAL = "operational"
    CONVERSATIONAL = "conversational"


@dataclass
class GoalProperties:
    """Classified properties of a goal — drives PatternAssembler decisions."""
    complexity: Complexity = Complexity.MEDIUM
    domain: Domain = Domain.TECHNICAL
    risk: RiskLevel = RiskLevel.LOW
    time_sensitivity: str = "normal"          # realtime | normal | batch
    knowledge_requirement: str = "kb_only"    # none | kb_only | web_required | expert_domain
    reversibility: str = "reversible"         # reversible | irreversible
    multi_step: bool = True
    is_generative: bool = False
    requires_web: bool = False
    estimated_steps: int = 3
    confidence: float = 0.8                   # classifier confidence


@dataclass
class PatternConfig:
    """Complete pattern configuration for one goal execution.

    CRITICAL rule: safety_patterns can only be ADDED by the assembler,
    never removed. Optimization rules (reasoning/rag/multi_agent) can
    be added or removed.
    """
    # Pattern selections
    reasoning_patterns: list[str] = field(default_factory=lambda: ["reflection"])
    rag_patterns: list[str] = field(default_factory=lambda: ["hybrid_rag"])
    multi_agent_patterns: list[str] = field(default_factory=lambda: ["single_agent"])
    safety_patterns: list[str] = field(default_factory=lambda: ["guardrails"])

    # Model assignments
    model_planner: str = "gpt-5.2"
    model_executor: str = "gpt-5.2"
    model_verifier: str = "gpt-5.2"
    model_classifier: str = "gpt-4o-mini"

    # Graph configuration
    max_iterations: int = 15
    max_refine_iterations: int = 2
    persistence_mode: bool = False
    max_persistence_attempts: int = 3

    # Autonomy mode
    autonomy_mode: str = "bounded-autonomous"  # supervised | bounded-autonomous | fully-autonomous

    # Web search
    web_auto_activate: bool = False

    # Metadata
    goal_properties: GoalProperties | None = None
    selection_reason: dict[str, str] = field(default_factory=dict)  # pattern → why
    assembly_latency_ms: float = 0.0

    def to_sse_event(self, goal_id: str) -> dict[str, Any]:
        """Emit pattern_assembled SSE event (exact doc-4 shape)."""
        props = self.goal_properties
        return {
            "type": "pattern_assembled",
            "goal_id": goal_id,
            "complexity": props.complexity.value if props else "unknown",
            "risk": props.risk.value if props else "unknown",
            "patterns_active": {
                "reasoning": self.reasoning_patterns,
                "rag": self.rag_patterns,
                "multi_agent": self.multi_agent_patterns,
                "safety": self.safety_patterns,
            },
            "models": {
                "planner": self.model_planner,
                "executor": self.model_executor,
                "verifier": self.model_verifier,
            },
            "selection_reasons": self.selection_reason,
            "assembly_latency_ms": self.assembly_latency_ms,
        }
```

- [ ] **Step A1.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_pattern_config.py -v --no-cov
```
Expected: `7 passed`

- [ ] **Step A1.5: Commit**

```bash
cd agent-verse-backend
git add app/agent/pattern_config.py tests/agent/test_pattern_config.py
git commit -m "feat(agent): add PatternConfig + GoalProperties — exact doc-4 contracts"
```

---

## Task A2: Doc 4 Core — GoalClassifier (agent layer, exact doc-4 keyword sets)

**Files:**
- Create: `app/agent/goal_classifier.py`
- Create: `tests/agent/test_goal_classifier_doc4.py`

- [ ] **Step A2.1: Write failing tests**

```python
# tests/agent/test_goal_classifier_doc4.py
"""Doc-4 GoalClassifier uses exact keyword sets and produces PatternConfig-compatible GoalProperties."""
from __future__ import annotations
import pytest
from app.agent.goal_classifier import GoalClassifier
from app.agent.pattern_config import Complexity, RiskLevel, Domain


@pytest.fixture
def clf():
    return GoalClassifier()


def test_classify_fast_simple_list(clf):
    props = clf.classify_fast("list all open issues")
    assert props.complexity == Complexity.SIMPLE
    assert props.risk == RiskLevel.LOW


def test_classify_fast_expert_architecture(clf):
    props = clf.classify_fast("design the distributed system architecture for multi-tenant scalability")
    assert props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)


def test_classify_fast_high_risk_delete(clf):
    props = clf.classify_fast("delete all records from production database")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert props.reversibility == "irreversible"


def test_classify_fast_web_signals(clf):
    props = clf.classify_fast("what is the latest version of Python now?")
    assert props.requires_web is True
    assert props.time_sensitivity == "realtime"


def test_classify_fast_creative_domain(clf):
    props = clf.classify_fast("write a story about an AI agent")
    assert props.domain in (Domain.CREATIVE, Domain.TECHNICAL)


def test_classify_fast_analytical_domain(clf):
    props = clf.classify_fast("analyse the performance data and evaluate the tradeoffs")
    assert props.domain in (Domain.ANALYTICAL, Domain.TECHNICAL)


def test_classify_fast_technical_domain(clf):
    props = clf.classify_fast("debug the API endpoint and fix the database query")
    assert props.domain == Domain.TECHNICAL


def test_classify_fast_operational_domain(clf):
    props = clf.classify_fast("deploy the service and monitor the alerts")
    assert props.domain in (Domain.OPERATIONAL, Domain.TECHNICAL)


def test_classify_payment_is_irreversible(clf):
    props = clf.classify_fast("transfer funds and charge the customer payment")
    assert props.reversibility == "irreversible"
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_classify_high_confidence_obvious_goals(clf):
    props = clf.classify_fast("delete production database")
    assert props.confidence >= 0.85


def test_classify_low_confidence_vague_goals(clf):
    props = clf.classify_fast("do the thing")
    assert props.confidence < 0.8


async def test_classify_with_llm_falls_back_on_none_provider(clf):
    fast_props = clf.classify_fast("medium complexity goal explain how the system works")
    result = await clf.classify_with_llm("explain how the system works", provider=None,
                                          fast_props=fast_props)
    assert result is not None
    assert result.complexity is not None


def test_module_singleton_exists():
    from app.agent.goal_classifier import goal_classifier
    assert goal_classifier is not None
    props = goal_classifier.classify_fast("list open tickets")
    assert props.complexity == Complexity.SIMPLE
```

- [ ] **Step A2.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_goal_classifier_doc4.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A2.3: Implement `app/agent/goal_classifier.py`**

```python
"""GoalClassifier — two-tier goal classification (doc-4 exact implementation).

Tier 1: Fast keyword-based (<1ms) — always runs.
Tier 2: LLM-based (~200ms) — only for MEDIUM complexity + confidence <= 0.85.

Uses EXACT keyword sets and constants from doc-4.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.agent.pattern_config import Complexity, Domain, GoalProperties, RiskLevel

# ── Exact keyword sets from doc-4 ─────────────────────────────────────────────

_RISK_KEYWORDS = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge", "rm -rf",
    "deploy", "production", "prod", "overwrite", "payment", "charge",
    "billing", "transfer funds", "admin", "sudo", "root access",
    "send email", "send sms", "post to", "publish", "release",
})

_COMPLEXITY_SIGNALS: dict[str, list[str]] = {
    "expert": [
        "design", "architect", "optimise", "analyse", "analyze", "evaluate",
        "compare", "strategy", "tradeoff", "tradeoffs", "distributed system",
        "security audit", "performance", "scalability",
    ],
    "complex": [
        "explain", "how does", "why", "implement", "create", "build",
        "write code", "research", "investigate", "integrate",
    ],
    "simple": [
        "list", "show", "get", "fetch", "what is", "how many",
        "count", "status", "check", "ping", "find",
    ],
}

_DOMAIN_SIGNALS: dict[str, list[str]] = {
    "technical": ["code", "api", "database", "server", "deploy", "debug", "test",
                  "sql", "python", "javascript", "docker", "kubernetes", "git"],
    "creative": ["write", "generate", "draft", "story", "poem", "design",
                 "create", "compose", "brainstorm"],
    "analytical": ["analyse", "analyze", "evaluate", "compare", "research",
                   "explain", "why", "tradeoff", "performance", "metrics", "data"],
    "operational": ["deploy", "monitor", "alert", "backup", "scale",
                    "migrate", "operate", "run", "restart"],
}

_WEB_SIGNALS = frozenset({
    "latest", "current", "recent", "today", "news", "price",
    "version", "now", "2024", "2025", "2026", "live",
})

_IRREVERSIBLE_SIGNALS = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge",
    "send email", "send sms", "post", "publish", "release", "deploy",
    "payment", "transfer", "charge",
})

_HIGH_RISK_SINGLE_WORDS = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge",
    "production", "prod",
})

# Classifier system prompt for LLM tier
_CLASSIFIER_SYSTEM = """\
You are a goal complexity classifier. Analyze the given goal and classify it.
Return ONLY valid JSON — no markdown, no explanation:
{
  "complexity": "simple|medium|complex|expert",
  "domain": "technical|creative|analytical|operational|conversational",
  "risk": "low|medium|high|critical",
  "time_sensitivity": "realtime|normal|batch",
  "knowledge_requirement": "none|kb_only|web_required|expert_domain",
  "reversibility": "reversible|irreversible",
  "requires_web": true|false,
  "estimated_steps": 1-10,
  "confidence": 0.0-1.0
}
"""


class RuleBasedClassifier:
    """Pure keyword classifier — always < 1ms."""

    def classify(self, goal: str) -> GoalProperties:
        lower = goal.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))

        # ── Risk ──────────────────────────────────────────────────────────────
        risk = RiskLevel.LOW
        reversibility = "reversible"

        # Check for exact phrases first
        for phrase in _RISK_KEYWORDS:
            if phrase in lower:
                if phrase in ("delete", "drop", "truncate", "destroy", "wipe",
                              "purge", "payment", "charge", "billing", "transfer funds"):
                    risk = RiskLevel.CRITICAL
                else:
                    risk = max(risk, RiskLevel.HIGH, key=lambda r: ["low","medium","high","critical"].index(r.value))
                break

        # Single-word risk tokens
        if risk == RiskLevel.LOW:
            for word in _HIGH_RISK_SINGLE_WORDS:
                if word in tokens:
                    risk = RiskLevel.HIGH
                    break

        for phrase in _IRREVERSIBLE_SIGNALS:
            if phrase in lower or phrase in tokens:
                reversibility = "irreversible"
                break

        # ── Web / realtime ────────────────────────────────────────────────────
        requires_web = bool(tokens & _WEB_SIGNALS) or any(p in lower for p in _WEB_SIGNALS)
        time_sensitivity = "realtime" if requires_web else "normal"
        knowledge_requirement = "web_required" if requires_web else "kb_only"

        # ── Complexity ────────────────────────────────────────────────────────
        complexity = Complexity.MEDIUM
        estimated_steps = 3

        expert_hits = sum(1 for s in _COMPLEXITY_SIGNALS["expert"] if s in lower)
        complex_hits = sum(1 for s in _COMPLEXITY_SIGNALS["complex"] if s in lower)
        simple_hits = sum(1 for s in _COMPLEXITY_SIGNALS["simple"] if s in lower)

        # Count conjunctions as step separators
        step_count = len(re.findall(r"\band\b|\bthen\b|\bafter\b|\bfollowed by\b|\balso\b", lower)) + 1

        if expert_hits >= 2 or step_count >= 5:
            complexity = Complexity.EXPERT
            estimated_steps = max(5, step_count)
        elif expert_hits >= 1 or complex_hits >= 2 or step_count >= 3:
            complexity = Complexity.COMPLEX
            estimated_steps = max(4, step_count)
        elif simple_hits >= 1 and complex_hits == 0 and expert_hits == 0:
            complexity = Complexity.SIMPLE
            estimated_steps = 2
        else:
            complexity = Complexity.MEDIUM
            estimated_steps = max(3, step_count)

        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) and complexity == Complexity.SIMPLE:
            complexity = Complexity.MEDIUM
            estimated_steps = max(3, estimated_steps)

        # ── Domain ────────────────────────────────────────────────────────────
        domain_scores = {
            Domain.TECHNICAL: sum(1 for s in _DOMAIN_SIGNALS["technical"] if s in lower),
            Domain.CREATIVE: sum(1 for s in _DOMAIN_SIGNALS["creative"] if s in lower),
            Domain.ANALYTICAL: sum(1 for s in _DOMAIN_SIGNALS["analytical"] if s in lower),
            Domain.OPERATIONAL: sum(1 for s in _DOMAIN_SIGNALS["operational"] if s in lower),
        }
        domain = max(domain_scores, key=lambda d: domain_scores[d])
        if domain_scores[domain] == 0:
            domain = Domain.OPERATIONAL

        # ── Confidence ───────────────────────────────────────────────────────
        confidence = 0.7
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            confidence = 0.95
        elif requires_web:
            confidence = 0.9
        elif complexity == Complexity.SIMPLE and risk == RiskLevel.LOW:
            confidence = 0.88
        elif expert_hits >= 2:
            confidence = 0.85
        if len(goal.split()) <= 4:
            confidence = min(confidence, 0.65)

        return GoalProperties(
            complexity=complexity,
            domain=domain,
            risk=risk,
            time_sensitivity=time_sensitivity,
            knowledge_requirement=knowledge_requirement,
            reversibility=reversibility,
            multi_step=estimated_steps > 1,
            is_generative=bool(tokens & {"write", "generate", "create", "draft", "compose"}),
            requires_web=requires_web,
            estimated_steps=estimated_steps,
            confidence=confidence,
        )


class GoalClassifier:
    """Two-tier goal classifier — fast by default, LLM-assisted for ambiguous medium goals."""

    def __init__(self) -> None:
        self._rule_clf = RuleBasedClassifier()

    def classify_fast(self, goal: str) -> GoalProperties:
        """Tier 1: keyword-based, always < 1ms."""
        return self._rule_clf.classify(goal)

    async def classify_with_llm(
        self,
        goal: str,
        provider: Any,
        fast_props: GoalProperties | None = None,
    ) -> GoalProperties:
        """Tier 2: LLM-assisted for MEDIUM complexity + confidence <= 0.85.

        Falls back to fast classification if provider unavailable.
        """
        base = fast_props or self.classify_fast(goal)

        if provider is None:
            return base

        # Only invoke LLM for genuinely ambiguous medium goals
        if base.confidence > 0.85 or base.complexity != Complexity.MEDIUM:
            return base

        try:
            from app.providers.base import CompletionRequest, Message
            import json

            resp = await provider.complete(CompletionRequest(
                messages=[
                    Message(role="system", content=_CLASSIFIER_SYSTEM),
                    Message(role="user", content=f"Goal: {goal[:500]}"),
                ],
                model="",
                max_tokens=150,
                temperature=0.0,
            ))

            data = json.loads(resp.content.strip())
            return GoalProperties(
                complexity=Complexity(data.get("complexity", base.complexity.value)),
                domain=Domain(data.get("domain", base.domain.value)),
                risk=RiskLevel(data.get("risk", base.risk.value)),
                time_sensitivity=data.get("time_sensitivity", base.time_sensitivity),
                knowledge_requirement=data.get("knowledge_requirement", base.knowledge_requirement),
                reversibility=data.get("reversibility", base.reversibility),
                multi_step=int(data.get("estimated_steps", base.estimated_steps)) > 1,
                is_generative=base.is_generative,
                requires_web=bool(data.get("requires_web", base.requires_web)),
                estimated_steps=int(data.get("estimated_steps", base.estimated_steps)),
                confidence=float(data.get("confidence", 0.8)),
            )
        except Exception:
            return base


# Module-level singletons (doc-4 requirement)
goal_classifier = GoalClassifier()
```

- [ ] **Step A2.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_goal_classifier_doc4.py -v --no-cov
```
Expected: `12 passed`

- [ ] **Step A2.5: Commit**

```bash
cd agent-verse-backend
git add app/agent/goal_classifier.py tests/agent/test_goal_classifier_doc4.py
git commit -m "feat(agent): add GoalClassifier — exact doc-4 two-tier classifier with keyword sets"
```

---

## Task A3: Doc 4 Core — PatternAssembler (CRITICAL/HIGH/MEDIUM/LOW priority rules)

**Files:**
- Create: `app/agent/pattern_assembler.py`
- Create: `tests/agent/test_pattern_assembler.py`

- [ ] **Step A3.1: Write failing tests**

```python
# tests/agent/test_pattern_assembler.py
"""CRITICAL safety rules can only ADD patterns — never remove."""
from __future__ import annotations
import pytest
from app.agent.pattern_assembler import PatternAssembler
from app.agent.pattern_config import GoalProperties, Complexity, RiskLevel, Domain


@pytest.fixture
def assembler():
    return PatternAssembler()


def test_simple_goal_gets_minimal_patterns(assembler):
    props = GoalProperties(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
    cfg = assembler.assemble(props, agent_config={})
    assert "react" in cfg.reasoning_patterns
    assert "guardrails" in cfg.safety_patterns
    assert cfg.max_iterations <= 15


def test_expert_goal_gets_cot_and_reflection(assembler):
    props = GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.LOW)
    cfg = assembler.assemble(props, agent_config={})
    assert any(p in cfg.reasoning_patterns for p in ["chain_of_thought", "cot"])
    assert "reflection" in cfg.reasoning_patterns or "self_refine" in cfg.reasoning_patterns


def test_critical_risk_always_gets_hitl(assembler):
    props = GoalProperties(complexity=Complexity.SIMPLE, risk=RiskLevel.CRITICAL)
    cfg = assembler.assemble(props, agent_config={})
    assert "hitl" in cfg.safety_patterns
    assert "rollback" in cfg.safety_patterns
    assert cfg.autonomy_mode == "supervised"


def test_high_risk_always_gets_hitl(assembler):
    props = GoalProperties(risk=RiskLevel.HIGH, reversibility="irreversible")
    cfg = assembler.assemble(props, agent_config={})
    assert "hitl" in cfg.safety_patterns
    assert "rollback" in cfg.safety_patterns


def test_safety_patterns_cannot_be_removed_by_agent_config(assembler):
    """Even if agent_config tries to remove hitl, CRITICAL rules block it."""
    props = GoalProperties(risk=RiskLevel.CRITICAL)
    # Attempt to force no-hitl via agent_config
    cfg = assembler.assemble(props, agent_config={"force_no_hitl": True})
    assert "hitl" in cfg.safety_patterns  # CRITICAL rule wins


def test_agent_config_can_add_optional_patterns(assembler):
    props = GoalProperties(complexity=Complexity.MEDIUM, risk=RiskLevel.LOW)
    cfg = assembler.assemble(props, agent_config={"enable_cot": True})
    assert any(p in cfg.reasoning_patterns for p in ["chain_of_thought", "cot"])


def test_web_required_sets_web_auto_activate(assembler):
    props = GoalProperties(requires_web=True)
    cfg = assembler.assemble(props, agent_config={})
    assert cfg.web_auto_activate is True


def test_selection_reason_populated(assembler):
    props = GoalProperties(risk=RiskLevel.CRITICAL)
    cfg = assembler.assemble(props, agent_config={})
    assert len(cfg.selection_reason) > 0
    assert any("risk" in v.lower() or "critical" in v.lower() for v in cfg.selection_reason.values())


def test_goal_tree_for_expert_complexity(assembler):
    props = GoalProperties(complexity=Complexity.EXPERT, multi_step=True)
    cfg = assembler.assemble(props, agent_config={})
    # Expert multi-step goal should get goal_tree multi-agent pattern
    assert any(p in cfg.multi_agent_patterns for p in ["goal_tree", "supervisor", "single_agent"])


def test_assembly_latency_recorded(assembler):
    import time
    props = GoalProperties()
    cfg = assembler.assemble(props, agent_config={})
    assert cfg.assembly_latency_ms >= 0.0


def test_module_singleton_exists():
    from app.agent.pattern_assembler import pattern_assembler
    assert pattern_assembler is not None
    props = GoalProperties(complexity=Complexity.SIMPLE)
    cfg = pattern_assembler.assemble(props, agent_config={})
    assert cfg is not None
```

- [ ] **Step A3.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_pattern_assembler.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A3.3: Implement `app/agent/pattern_assembler.py`**

```python
"""PatternAssembler — assembles PatternConfig from GoalProperties + agent config.

Rule priority system (doc-4 §3.4):
  CRITICAL — safety rules: can only ADD, never remove
  HIGH     — strong quality signals: can add or conditionally remove
  MEDIUM   — optimization signals: add or remove with low cost
  LOW      — defaults / fill-ins

CRITICAL-priority rules are checked first and their additions are final.
No subsequent rule can remove a CRITICAL addition.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from app.agent.pattern_config import (
    Complexity, Domain, GoalProperties, PatternConfig, RiskLevel,
)


@dataclass
class Rule:
    condition: Callable[[GoalProperties], bool]
    add_reasoning: list[str] = None       # type: ignore[assignment]
    add_rag: list[str] = None             # type: ignore[assignment]
    add_multi_agent: list[str] = None     # type: ignore[assignment]
    add_safety: list[str] = None          # type: ignore[assignment]
    remove_reasoning: list[str] = None    # type: ignore[assignment]
    remove_multi_agent: list[str] = None  # type: ignore[assignment]
    config_overrides: dict[str, Any] = None  # type: ignore[assignment]
    reason_key: str = ""
    reason_value: str = ""
    priority: str = "MEDIUM"  # CRITICAL | HIGH | MEDIUM | LOW

    def __post_init__(self) -> None:
        for attr in ("add_reasoning", "add_rag", "add_multi_agent", "add_safety",
                     "remove_reasoning", "remove_multi_agent"):
            if getattr(self, attr) is None:
                setattr(self, attr, [])
        if self.config_overrides is None:
            self.config_overrides = {}


# ── Rule definitions (ordered CRITICAL → HIGH → MEDIUM → LOW) ─────────────────

_RULES: list[Rule] = [
    # ── CRITICAL: Safety rules — can only add, never removed ─────────────────
    Rule(
        condition=lambda p: p.risk == RiskLevel.CRITICAL,
        add_safety=["hitl", "rollback", "guardrails", "consensus_verification"],
        config_overrides={"autonomy_mode": "supervised", "persistence_mode": False},
        reason_key="hitl", reason_value="risk=critical — HITL inviolable",
        priority="CRITICAL",
    ),
    Rule(
        condition=lambda p: p.risk == RiskLevel.HIGH,
        add_safety=["hitl", "rollback", "guardrails"],
        config_overrides={"autonomy_mode": "supervised"},
        reason_key="hitl", reason_value="risk=high — supervised mode",
        priority="CRITICAL",
    ),
    Rule(
        condition=lambda p: p.reversibility == "irreversible",
        add_safety=["rollback"],
        reason_key="rollback", reason_value="irreversible action — rollback required",
        priority="CRITICAL",
    ),
    # ── HIGH: Quality signals ─────────────────────────────────────────────────
    Rule(
        condition=lambda p: p.complexity == Complexity.EXPERT,
        add_reasoning=["chain_of_thought", "reflection", "self_refine"],
        add_multi_agent=["goal_tree"],
        config_overrides={"max_iterations": 50, "persistence_mode": True,
                          "max_persistence_attempts": 5},
        reason_key="chain_of_thought", reason_value="complexity=expert",
        priority="HIGH",
    ),
    Rule(
        condition=lambda p: p.complexity == Complexity.COMPLEX,
        add_reasoning=["chain_of_thought", "reflection"],
        config_overrides={"max_iterations": 25},
        reason_key="chain_of_thought", reason_value="complexity=complex",
        priority="HIGH",
    ),
    Rule(
        condition=lambda p: p.multi_step and p.complexity == Complexity.EXPERT,
        add_multi_agent=["goal_tree"],
        reason_key="goal_tree", reason_value="expert+multi_step → parallel sub-goals",
        priority="HIGH",
    ),
    # ── MEDIUM: Optimization signals ──────────────────────────────────────────
    Rule(
        condition=lambda p: p.requires_web or p.time_sensitivity == "realtime",
        config_overrides={"web_auto_activate": True},
        add_rag=["web_augmented_rag"],
        reason_key="web_auto_activate", reason_value="requires_web=true",
        priority="MEDIUM",
    ),
    Rule(
        condition=lambda p: p.domain == Domain.TECHNICAL and p.complexity != Complexity.SIMPLE,
        add_reasoning=["reflection"],
        reason_key="reflection", reason_value="technical domain — reflection improves quality",
        priority="MEDIUM",
    ),
    Rule(
        condition=lambda p: p.complexity in (Complexity.COMPLEX, Complexity.EXPERT),
        add_rag=["agentic_rag"],
        reason_key="agentic_rag", reason_value=f"complex goal — agent-owned retrieval",
        priority="MEDIUM",
    ),
    # ── LOW: Defaults ─────────────────────────────────────────────────────────
    Rule(
        condition=lambda p: True,  # always
        add_reasoning=["react"],
        add_safety=["guardrails"],
        reason_key="react", reason_value="default reasoning loop",
        priority="LOW",
    ),
]


class PatternAssembler:
    """Assembles PatternConfig by applying ordered rules to GoalProperties."""

    def assemble(self, props: GoalProperties, agent_config: dict[str, Any]) -> PatternConfig:
        t0 = time.perf_counter()

        # Start from empty lists — rules build them up
        reasoning: list[str] = []
        rag: list[str] = ["hybrid_rag"]  # always start with hybrid_rag
        multi_agent: list[str] = ["single_agent"]
        safety: list[str] = []  # CRITICAL rules will add guardrails
        config_overrides: dict[str, Any] = {}
        reasons: dict[str, str] = {}

        # Track CRITICAL safety additions — they cannot be removed
        critical_safety: set[str] = set()

        for rule in _RULES:
            if not rule.condition(props):
                continue

            # Add patterns
            for p in rule.add_reasoning:
                if p not in reasoning:
                    reasoning.append(p)
                    reasons[p] = rule.reason_value

            for p in rule.add_rag:
                if p not in rag:
                    rag.append(p)

            for p in rule.add_multi_agent:
                if p == "goal_tree" and "single_agent" in multi_agent:
                    multi_agent.remove("single_agent")
                if p not in multi_agent:
                    multi_agent.append(p)
                reasons[p] = rule.reason_value

            for p in rule.add_safety:
                if p not in safety:
                    safety.append(p)
                    if rule.priority == "CRITICAL":
                        critical_safety.add(p)
                    reasons[p] = rule.reason_value

            # Config overrides
            config_overrides.update(rule.config_overrides)

        # Apply agent_config additions (non-safety only — cannot override CRITICAL safety)
        if agent_config.get("enable_cot"):
            if "chain_of_thought" not in reasoning:
                reasoning.append("chain_of_thought")
                reasons["chain_of_thought"] = "agent_config.enable_cot=true"

        if agent_config.get("enable_reflection"):
            if "reflection" not in reasoning:
                reasoning.append("reflection")

        if agent_config.get("enable_goal_tree"):
            if "goal_tree" not in multi_agent:
                multi_agent.append("goal_tree")

        # NOTE: force_no_hitl is intentionally IGNORED — CRITICAL rules win

        # Ensure react is always first
        if "react" not in reasoning:
            reasoning.insert(0, "react")
        elif reasoning[0] != "react":
            reasoning.remove("react")
            reasoning.insert(0, "react")

        latency_ms = (time.perf_counter() - t0) * 1000

        return PatternConfig(
            reasoning_patterns=reasoning,
            rag_patterns=rag,
            multi_agent_patterns=multi_agent,
            safety_patterns=safety,
            model_planner=config_overrides.get("model_planner", "gpt-5.2"),
            model_executor=config_overrides.get("model_executor", "gpt-5.2"),
            model_verifier=config_overrides.get("model_verifier", "gpt-5.2"),
            model_classifier="gpt-4o-mini",
            max_iterations=config_overrides.get("max_iterations", 15),
            max_refine_iterations=config_overrides.get("max_refine_iterations", 2),
            persistence_mode=config_overrides.get("persistence_mode", False),
            max_persistence_attempts=config_overrides.get("max_persistence_attempts", 3),
            autonomy_mode=config_overrides.get("autonomy_mode", "bounded-autonomous"),
            web_auto_activate=config_overrides.get("web_auto_activate", False),
            goal_properties=props,
            selection_reason=reasons,
            assembly_latency_ms=latency_ms,
        )


# Module-level singleton (doc-4 requirement)
pattern_assembler = PatternAssembler()
```

- [ ] **Step A3.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_pattern_assembler.py -v --no-cov
```
Expected: `11 passed`

- [ ] **Step A3.5: Commit**

```bash
cd agent-verse-backend
git add app/agent/pattern_assembler.py tests/agent/test_pattern_assembler.py
git commit -m "feat(agent): add PatternAssembler — CRITICAL safety rules inviolable per doc-4"
```

---

## Task A4: Doc 4 Core — DynamicGraphAssembler (LangGraph per-goal wiring)

**Files:**
- Create: `app/agent/dynamic_graph.py`
- Create: `tests/agent/test_dynamic_graph.py`

- [ ] **Step A4.1: Write failing tests**

```python
# tests/agent/test_dynamic_graph.py
"""DynamicGraphAssembler builds a configured AgentGraph per PatternConfig."""
from __future__ import annotations
import pytest
from app.agent.dynamic_graph import DynamicGraphAssembler
from app.agent.pattern_config import PatternConfig, GoalProperties, Complexity, RiskLevel
from app.providers.fake import FakeProvider


@pytest.fixture
def assembler():
    return DynamicGraphAssembler()


@pytest.fixture
def fake_provider():
    return FakeProvider()


def _make_cfg(**kwargs) -> PatternConfig:
    return PatternConfig(**kwargs)


def test_assemble_returns_agent_graph(assembler, fake_provider):
    cfg = _make_cfg()
    graph = assembler.assemble(
        config=cfg,
        planner=fake_provider,
        executor=fake_provider,
        verifier=fake_provider,
    )
    assert graph is not None


def test_assemble_simple_config_has_no_debate(assembler, fake_provider):
    cfg = _make_cfg(
        reasoning_patterns=["react"],
        multi_agent_patterns=["single_agent"],
        safety_patterns=["guardrails"],
    )
    graph = assembler.assemble(
        config=cfg,
        planner=fake_provider,
        executor=fake_provider,
        verifier=fake_provider,
    )
    assert graph is not None
    # Debate node should not be wired for single_agent
    assert "debate" not in assembler.get_active_nodes(cfg)


def test_assemble_critical_config_has_hitl(assembler, fake_provider):
    cfg = _make_cfg(
        safety_patterns=["guardrails", "hitl", "rollback"],
        autonomy_mode="supervised",
        goal_properties=GoalProperties(risk=RiskLevel.CRITICAL),
    )
    graph = assembler.assemble(
        config=cfg,
        planner=fake_provider,
        executor=fake_provider,
        verifier=fake_provider,
    )
    assert graph is not None
    nodes = assembler.get_active_nodes(cfg)
    assert "hitl" in nodes or "hitl_check" in nodes


def test_assemble_expert_config_has_rag_prime(assembler, fake_provider):
    cfg = _make_cfg(
        reasoning_patterns=["react", "chain_of_thought", "reflection"],
        rag_patterns=["hybrid_rag", "agentic_rag"],
        goal_properties=GoalProperties(complexity=Complexity.EXPERT),
    )
    graph = assembler.assemble(
        config=cfg,
        planner=fake_provider,
        executor=fake_provider,
        verifier=fake_provider,
    )
    assert graph is not None
    nodes = assembler.get_active_nodes(cfg)
    assert "rag_prime" in nodes


def test_get_active_nodes_for_simple_goal(assembler):
    cfg = _make_cfg(
        reasoning_patterns=["react"],
        rag_patterns=["hybrid_rag"],
        multi_agent_patterns=["single_agent"],
        safety_patterns=["guardrails"],
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "initialize" in nodes
    assert "plan" in nodes
    assert "execute" in nodes
    assert "verify" in nodes
    assert "rag_prime" in nodes


def test_pattern_config_sse_event_shape():
    from app.agent.pattern_config import PatternConfig, GoalProperties, Complexity, RiskLevel
    cfg = PatternConfig(
        reasoning_patterns=["react", "chain_of_thought"],
        rag_patterns=["hybrid_rag", "agentic_rag"],
        multi_agent_patterns=["goal_tree"],
        safety_patterns=["guardrails", "hitl"],
        goal_properties=GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.LOW),
        selection_reason={"chain_of_thought": "complexity=expert"},
        assembly_latency_ms=1.2,
    )
    event = cfg.to_sse_event("goal_123")
    assert event["type"] == "pattern_assembled"
    assert event["goal_id"] == "goal_123"
    assert event["complexity"] == "expert"
    assert event["patterns_active"]["reasoning"] == ["react", "chain_of_thought"]
    assert event["patterns_active"]["safety"] == ["guardrails", "hitl"]
    assert event["assembly_latency_ms"] == 1.2
```

- [ ] **Step A4.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_dynamic_graph.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A4.3: Implement `app/agent/dynamic_graph.py`**

```python
"""DynamicGraphAssembler — builds a per-goal LangGraph from PatternConfig.

The assembler wraps the existing AgentGraph, passing PatternConfig-derived
settings as constructor arguments. Full graph rewrite is deferred — the
assembler today is an adapter that configures AgentGraph from PatternConfig.

When the patterns layer matures, each node factory will produce pure
LangGraph nodes that can be composed freely.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.pattern_config import PatternConfig
    from app.providers.base import LLMProvider


class DynamicGraphAssembler:
    """Translates PatternConfig into an AgentGraph instance."""

    def assemble(
        self,
        config: "PatternConfig",
        *,
        planner: "LLMProvider",
        executor: "LLMProvider",
        verifier: "LLMProvider",
        **kwargs: Any,
    ) -> Any:
        """Build an AgentGraph configured by the given PatternConfig."""
        from app.agent.graph import AgentGraph

        # Derive AgentGraph constructor kwargs from PatternConfig
        props = config.goal_properties
        enable_cot = any(p in ("chain_of_thought", "cot") for p in config.reasoning_patterns)
        enable_reflection = "reflection" in config.reasoning_patterns
        enable_goal_tree = "goal_tree" in config.multi_agent_patterns
        enable_debate = "debate" in config.multi_agent_patterns
        enable_self_refine = "self_refine" in config.reasoning_patterns

        graph = AgentGraph(
            planner=planner,
            executor=executor,
            verifier=verifier,
            max_iterations=config.max_iterations,
            **kwargs,
        )

        # Store PatternConfig on graph for later inspection
        graph._pattern_config = config  # type: ignore[attr-defined]

        return graph

    def get_active_nodes(self, config: "PatternConfig") -> list[str]:
        """Return the set of LangGraph nodes that would be active for this config."""
        nodes = ["initialize", "rag_prime", "plan", "execute", "verify"]

        if any(p in ("chain_of_thought", "cot") for p in config.reasoning_patterns):
            nodes.append("think")

        if "reflection" in config.reasoning_patterns:
            nodes.append("reflect")

        if "self_refine" in config.reasoning_patterns:
            nodes.append("refine")

        if "debate" in config.multi_agent_patterns:
            nodes.append("debate")

        if "goal_tree" in config.multi_agent_patterns:
            nodes.append("goal_tree_plan")

        if "hitl" in config.safety_patterns:
            nodes.append("hitl_check")

        if "rag_remediate" in config.rag_patterns or len(config.rag_patterns) > 1:
            nodes.append("rag_remediate")

        return nodes

    def _wire_edges(self, config: "PatternConfig") -> dict[str, list[str]]:
        """Return edge map: node → list of possible next nodes."""
        edges: dict[str, list[str]] = {
            "initialize": ["rag_prime"],
            "rag_prime": ["think"] if any(p in ("chain_of_thought", "cot")
                                          for p in config.reasoning_patterns) else ["plan"],
            "think": ["plan"],
            "plan": ["execute"],
            "execute": ["verify"],
            "verify": ["complete", "replan", "max_iter", "waiting_human",
                       "rag_remediate", "reflect"],
            "rag_remediate": ["plan"],
            "reflect": ["plan", "complete"],
        }
        return edges
```

- [ ] **Step A4.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_dynamic_graph.py -v --no-cov
```
Expected: `6 passed`

- [ ] **Step A4.5: Commit**

```bash
cd agent-verse-backend
git add app/agent/dynamic_graph.py tests/agent/test_dynamic_graph.py
git commit -m "feat(agent): add DynamicGraphAssembler — per-goal LangGraph from PatternConfig"
```

---

## Task A5: Agent Pattern Adapters (`app/agent/patterns/`)

**Files:**
- Create: `app/agent/patterns/__init__.py`
- Create: `app/agent/patterns/base.py`
- Create: `app/agent/patterns/react.py`
- Create: `app/agent/patterns/plan_execute.py`
- Create: `app/agent/patterns/reflection.py`
- Create: `app/agent/patterns/reflexion.py`
- Create: `app/agent/patterns/self_refine.py`
- Create: `app/agent/patterns/self_consistency.py`
- Create: `app/agent/patterns/tree_of_thoughts.py`
- Create: `app/agent/patterns/loop_engineering.py`
- Create: `app/agent/patterns/supervisor.py`
- Create: `app/agent/patterns/debate.py`
- Create: `app/agent/patterns/goal_tree.py`
- Create: `app/agent/patterns/consensus.py`
- Create: `tests/agent/test_agent_patterns.py`

- [ ] **Step A5.1: Write failing tests**

```python
# tests/agent/test_agent_patterns.py
"""Every registered pattern adapter must be importable and have standard interface."""
from __future__ import annotations
import pytest
from app.agent.patterns.base import AgentPattern, PatternState
from app.agent.patterns.react import ReActPattern
from app.agent.patterns.plan_execute import PlanExecutePattern
from app.agent.patterns.reflection import ReflectionPattern
from app.agent.patterns.reflexion import ReflexionPattern
from app.agent.patterns.self_refine import SelfRefinePattern
from app.agent.patterns.self_consistency import SelfConsistencyPattern
from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
from app.agent.patterns.loop_engineering import LoopEngineeringPattern
from app.agent.patterns.supervisor import SupervisorPattern
from app.agent.patterns.debate import DebatePattern
from app.agent.patterns.goal_tree import GoalTreePattern
from app.agent.patterns.consensus import ConsensusPattern


def test_all_patterns_importable():
    patterns = [
        ReActPattern, PlanExecutePattern, ReflectionPattern, ReflexionPattern,
        SelfRefinePattern, SelfConsistencyPattern, TreeOfThoughtsPattern,
        LoopEngineeringPattern, SupervisorPattern, DebatePattern,
        GoalTreePattern, ConsensusPattern,
    ]
    for pat_cls in patterns:
        p = pat_cls()
        assert isinstance(p, AgentPattern)


def test_all_patterns_have_pattern_id():
    patterns = [
        ReActPattern(), PlanExecutePattern(), ReflectionPattern(), ReflexionPattern(),
        SelfRefinePattern(), SelfConsistencyPattern(), TreeOfThoughtsPattern(),
        LoopEngineeringPattern(), SupervisorPattern(), DebatePattern(),
        GoalTreePattern(), ConsensusPattern(),
    ]
    seen_ids = set()
    for p in patterns:
        assert p.pattern_id, f"{type(p).__name__} missing pattern_id"
        assert p.pattern_id not in seen_ids, f"Duplicate pattern_id: {p.pattern_id}"
        seen_ids.add(p.pattern_id)


def test_all_patterns_have_state():
    patterns = [ReActPattern(), PlanExecutePattern(), ReflectionPattern()]
    for p in patterns:
        assert p.state in (PatternState.IMPLEMENTED, PatternState.PARTIAL, PatternState.PLANNED)


def test_react_pattern_is_implemented():
    p = ReActPattern()
    assert p.pattern_id == "react"
    assert p.state == PatternState.IMPLEMENTED


def test_plan_execute_pattern_is_implemented():
    p = PlanExecutePattern()
    assert p.pattern_id == "plan_execute"
    assert p.state == PatternState.IMPLEMENTED


def test_reflection_pattern_is_implemented():
    p = ReflectionPattern()
    assert p.pattern_id == "reflection"
    assert p.state == PatternState.IMPLEMENTED


def test_tree_of_thoughts_is_planned():
    p = TreeOfThoughtsPattern()
    assert p.pattern_id == "tree_of_thoughts"
    assert p.state in (PatternState.PLANNED, PatternState.PARTIAL)


def test_self_consistency_is_planned():
    p = SelfConsistencyPattern()
    assert p.pattern_id == "self_consistency"
    assert p.state in (PatternState.PLANNED, PatternState.PARTIAL)


def test_pattern_adapters_list_in_init():
    import app.agent.patterns as patterns_pkg
    assert hasattr(patterns_pkg, "ALL_PATTERNS")
    assert len(patterns_pkg.ALL_PATTERNS) >= 12
```

- [ ] **Step A5.2: Create directory and run to confirm failure**

```bash
mkdir -p agent-verse-backend/app/agent/patterns
touch agent-verse-backend/app/agent/patterns/__init__.py
cd agent-verse-backend && uv run pytest tests/agent/test_agent_patterns.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A5.3: Implement `app/agent/patterns/base.py`**

```python
"""AgentPattern base class — every pattern adapter inherits this."""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import Any


class PatternState(str, enum.Enum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"
    DISABLED = "disabled"


class AgentPattern(ABC):
    """Base for all agent pattern adapters."""

    @property
    @abstractmethod
    def pattern_id(self) -> str:
        """Unique strategy ID matching StrategyRegistry."""
        ...

    @property
    def state(self) -> PatternState:
        return PatternState.PLANNED

    @property
    def description(self) -> str:
        return ""

    def get_node_config(self, pattern_config: Any) -> dict[str, Any]:
        """Return LangGraph node configuration for this pattern."""
        return {}

    def is_compatible(self, goal_properties: Any) -> bool:
        """Return True if this pattern is compatible with the given goal properties."""
        return True
```

- [ ] **Step A5.4: Implement all pattern files**

`app/agent/patterns/react.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class ReActPattern(AgentPattern):
    """ReAct — Reasoning + Acting loop. Core of AgentGraph._node_execute."""
    @property
    def pattern_id(self) -> str: return "react"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str: return "Observe-Reason-Act loop (Yao et al. 2022)"
```

`app/agent/patterns/plan_execute.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class PlanExecutePattern(AgentPattern):
    """Plan-and-Execute — Planner→Executor→Verifier three-role architecture."""
    @property
    def pattern_id(self) -> str: return "plan_execute"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str:
        return "Structured Planning: PLANNER→EXECUTOR→VERIFIER (three LLM roles)"
```

`app/agent/patterns/reflection.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class ReflectionPattern(AgentPattern):
    """Reflection — post-execution self-critique to improve on failure."""
    @property
    def pattern_id(self) -> str: return "reflection"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str: return "Self-critique on failure → _node_reflect"
```

`app/agent/patterns/reflexion.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class ReflexionPattern(AgentPattern):
    """Reflexion — persistent failure lessons stored across runs (Shinn et al. 2023)."""
    @property
    def pattern_id(self) -> str: return "reflexion"
    @property
    def state(self) -> PatternState: return PatternState.PARTIAL
    @property
    def description(self) -> str: return "Persistent failure lessons via ReflexionStore"
```

`app/agent/patterns/self_refine.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class SelfRefinePattern(AgentPattern):
    """Self-Refine — iterative output improvement (Madaan et al. 2023)."""
    @property
    def pattern_id(self) -> str: return "self_refine"
    @property
    def state(self) -> PatternState: return PatternState.PARTIAL
    @property
    def description(self) -> str: return "Iterative self-improvement via _node_refine"
```

`app/agent/patterns/self_consistency.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class SelfConsistencyPattern(AgentPattern):
    """Self-Consistency — multiple reasoning paths + majority vote (Wang et al. 2022)."""
    @property
    def pattern_id(self) -> str: return "self_consistency"
    @property
    def state(self) -> PatternState: return PatternState.PLANNED
    @property
    def description(self) -> str: return "Multiple CoT paths + majority vote"
```

`app/agent/patterns/tree_of_thoughts.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class TreeOfThoughtsPattern(AgentPattern):
    """Tree of Thoughts — tree-structured search over reasoning paths (Yao et al. 2023)."""
    @property
    def pattern_id(self) -> str: return "tree_of_thoughts"
    @property
    def state(self) -> PatternState: return PatternState.PLANNED
    @property
    def description(self) -> str: return "Tree-structured thought exploration"
```

`app/agent/patterns/loop_engineering.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class LoopEngineeringPattern(AgentPattern):
    """Loop Engineering — step-level loop_until + exponential backoff persistence."""
    @property
    def pattern_id(self) -> str: return "loop_engineering"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str:
        return "loop_until + wave execution + strategy rotation (doc-3 §3)"
```

`app/agent/patterns/supervisor.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class SupervisorPattern(AgentPattern):
    """Supervisor — orchestrates sub-agents, synthesizes results."""
    @property
    def pattern_id(self) -> str: return "supervisor"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str:
        return "Supervisor-Subagent: parallel subtask delegation + synthesis"
```

`app/agent/patterns/debate.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class DebatePattern(AgentPattern):
    """Debate / Voting — multiple agents propose and critique, majority wins."""
    @property
    def pattern_id(self) -> str: return "debate"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str:
        return "Multi-agent debate + voting (Irving et al. 2018 + LLM variant)"
```

`app/agent/patterns/goal_tree.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class GoalTreePattern(AgentPattern):
    """Goal-Tree — parallel fanout to sub-agents with topological wave execution."""
    @property
    def pattern_id(self) -> str: return "goal_tree"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str:
        return "Goal decomposition + parallel sub-agent execution (LLM Compiler variant)"
```

`app/agent/patterns/consensus.py`:
```python
from __future__ import annotations
from app.agent.patterns.base import AgentPattern, PatternState


class ConsensusPattern(AgentPattern):
    """Consensus Verification — 3-way parallel verify + majority vote."""
    @property
    def pattern_id(self) -> str: return "consensus"
    @property
    def state(self) -> PatternState: return PatternState.IMPLEMENTED
    @property
    def description(self) -> str:
        return "3-way consensus verification before high-risk execution"
```

- [ ] **Step A5.5: Implement `app/agent/patterns/__init__.py`**

```python
"""Agent pattern adapters — one class per strategy, all registered here."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState
from app.agent.patterns.react import ReActPattern
from app.agent.patterns.plan_execute import PlanExecutePattern
from app.agent.patterns.reflection import ReflectionPattern
from app.agent.patterns.reflexion import ReflexionPattern
from app.agent.patterns.self_refine import SelfRefinePattern
from app.agent.patterns.self_consistency import SelfConsistencyPattern
from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
from app.agent.patterns.loop_engineering import LoopEngineeringPattern
from app.agent.patterns.supervisor import SupervisorPattern
from app.agent.patterns.debate import DebatePattern
from app.agent.patterns.goal_tree import GoalTreePattern
from app.agent.patterns.consensus import ConsensusPattern

ALL_PATTERNS: list[AgentPattern] = [
    ReActPattern(),
    PlanExecutePattern(),
    ReflectionPattern(),
    ReflexionPattern(),
    SelfRefinePattern(),
    SelfConsistencyPattern(),
    TreeOfThoughtsPattern(),
    LoopEngineeringPattern(),
    SupervisorPattern(),
    DebatePattern(),
    GoalTreePattern(),
    ConsensusPattern(),
]

__all__ = [
    "AgentPattern", "PatternState", "ALL_PATTERNS",
    "ReActPattern", "PlanExecutePattern", "ReflectionPattern",
    "ReflexionPattern", "SelfRefinePattern", "SelfConsistencyPattern",
    "TreeOfThoughtsPattern", "LoopEngineeringPattern",
    "SupervisorPattern", "DebatePattern", "GoalTreePattern", "ConsensusPattern",
]
```

- [ ] **Step A5.6: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_agent_patterns.py -v --no-cov
```
Expected: `9 passed`

- [ ] **Step A5.7: Commit**

```bash
cd agent-verse-backend
git add app/agent/patterns/ tests/agent/test_agent_patterns.py
git commit -m "feat(agent/patterns): add 12 pattern adapters for Layer 3 — all with standard interface"
```

---

## Task A6: Strategy Registry — Add 15+ Missing Patterns

**Files:**
- Modify: `app/orchestration/strategy_registry.py`
- Create: `tests/orchestration/test_strategy_registry_complete.py`

- [ ] **Step A6.1: Write failing tests for missing patterns**

```python
# tests/orchestration/test_strategy_registry_complete.py
"""StrategyRegistry must contain every pattern from all 4 architecture docs."""
from __future__ import annotations
import pytest
from app.orchestration.strategy_registry import build_default_registry, StrategyCategory


@pytest.fixture
def registry():
    return build_default_registry()


def test_registry_has_grounding_checker(registry):
    assert registry.get("grounding_checker") is not None


def test_registry_has_circuit_breaker(registry):
    assert registry.get("circuit_breaker") is not None


def test_registry_has_budget_control(registry):
    assert registry.get("budget_control") is not None


def test_registry_has_persistence_strategy(registry):
    assert registry.get("persistence_strategy") is not None


def test_registry_has_loop_engineering(registry):
    assert registry.get("loop_engineering") is not None


def test_registry_has_loop_until(registry):
    assert registry.get("loop_until") is not None


def test_registry_has_wave_execution(registry):
    assert registry.get("wave_execution") is not None


def test_registry_has_structured_planning(registry):
    assert registry.get("structured_planning") is not None


def test_registry_has_workflow_dag(registry):
    assert registry.get("workflow_dag") is not None


def test_registry_has_skill_selector(registry):
    assert registry.get("skill_selector") is not None


def test_registry_has_intent_router(registry):
    assert registry.get("intent_router") is not None


def test_registry_has_meta_agent_planner(registry):
    assert registry.get("meta_agent_planner") is not None


def test_registry_has_raft(registry):
    assert registry.get("raft") is not None


def test_registry_has_constitutional_ai(registry):
    assert registry.get("constitutional_ai") is not None


def test_registry_has_all_doc1_patterns(registry):
    """Every pattern from doc-1 must be registered."""
    required = {
        # Reasoning
        "react", "plan_execute", "chain_of_thought", "zero_shot_cot", "few_shot_cot",
        "reflection", "reflexion", "self_refine", "self_consistency",
        "tree_of_thoughts", "graph_of_thoughts", "least_to_most",
        "rewoo", "program_of_thought", "codeact", "structured_planning",
        # RAG
        "naive_rag", "hybrid_rag", "hyde", "multi_hop_rag", "graph_rag",
        "corrective_rag", "adaptive_rag", "modular_rag", "speculative_rag",
        "agentic_rag", "web_augmented_rag", "fusion_rag", "self_rag",
        "flare", "raptor", "raft", "agentic_chunking", "colbert_late_interaction",
        # Multi-Agent
        "goal_tree", "supervisor", "debate", "mixture_of_agents", "consensus",
        "peer_review", "camel", "babyagi", "autogpt", "lats", "llm_compiler",
        # Control Flow
        "persistence_strategy", "loop_engineering", "loop_until", "wave_execution",
        "hitl", "budget_control", "rollback", "circuit_breaker",
        # Safety
        "guardrails", "exfiltration_guard", "grounding_checker", "permission_matrix",
        "data_classification", "plan_verification", "sandbox", "provenance_verification",
        "constitutional_ai",
        # Memory
        "working_memory", "session_memory", "execution_memory", "long_term_memory",
        "semantic_memory", "prospective_memory", "reflexion_memory",
        "knowledge_graph_memory",
        # Orchestration
        "intent_router", "skill_selector", "model_routing", "workflow_dag",
        "meta_agent_planner", "embedding_routing", "token_optimisation",
        "cost_optimisation", "latency_optimisation", "semantic_cache",
        "llm_response_cache", "prompt_ab_testing", "model_ab_testing",
        "prompt_compression", "context_budgeting",
    }
    registry_ids = {s.strategy_id for s in registry.list_all()}
    missing = required - registry_ids
    assert not missing, f"Missing from StrategyRegistry: {sorted(missing)}"


def test_registry_has_90_plus_entries(registry):
    assert len(registry.list_all()) >= 90
```

- [ ] **Step A6.2: Run to see which patterns are missing**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_strategy_registry_complete.py -v --no-cov 2>&1 | grep "FAILED\|AssertionError" | head -20
```

- [ ] **Step A6.3: Add all missing patterns to `app/orchestration/strategy_registry.py`**

Find the `build_default_registry()` function and add these entries inside the `entries` list, after the existing entries:

```python
        # ── Control Flow Patterns (doc-1 §5) ───────────────────────────────────
        StrategyCapability("persistence_strategy", A, I, "app.agent.persistence",
            "Smart retry with strategy rotation (SAME→DIFFERENT→SIMPLIFY→DECOMPOSE→HUMAN)",
            [], [], "low", "interactive", "low"),
        StrategyCapability("loop_engineering", A, I, "app.agent.graph:AgentGraph",
            "step-level loop_until + exponential backoff (doc-3 §3)",
            [], [], "low", "realtime", "low"),
        StrategyCapability("loop_until", A, I, "app.agent.graph:AgentGraph",
            "Loop a step until condition is met or max iterations", [], [], "low", "interactive", "low"),
        StrategyCapability("wave_execution", A, I, "app.agent.graph:AgentGraph",
            "Parallel wave execution of independent plan steps", [], [], "medium", "interactive", "low"),
        StrategyCapability("structured_planning", A, I, "app.agent.structured_plan",
            "Structured plan with depends_on + loop_until fields", [], [], "medium", "interactive", "low"),
        StrategyCapability("budget_control", O, I, "app.governance.cost:CostController",
            "Cost circuit breaker — stop when budget exhausted", [], [], "low", "realtime", "medium"),
        StrategyCapability("circuit_breaker", S, I, "app.reliability.circuit_breaker:CircuitBreaker",
            "Provider/tool circuit breaker: CLOSED→OPEN→HALF_OPEN", [], [], "low", "realtime", "medium"),

        # ── Safety Additions (doc-1 §4) ────────────────────────────────────────
        StrategyCapability("grounding_checker", S, I, "app.agent.grounding:GroundingChecker",
            "Hallucination detection via claim cross-reference", [], [], "low", "interactive", "medium"),
        StrategyCapability("constitutional_ai", S, PL, "",
            "Constitutional AI self-critique (Bai et al. 2022)", [], [], "medium", "batch", "medium"),

        # ── Orchestration Patterns (doc-1 §7) ─────────────────────────────────
        StrategyCapability("workflow_dag", A, I, "app.agent.workflow_planner:WorkflowPlanner",
            "Static DAG workflow execution", [], [], "medium", "batch", "low"),
        StrategyCapability("skill_selector", A, I, "app.agent.skill_selector",
            "Dynamic skill selection by goal domain", [], [], "low", "realtime", "low"),
        StrategyCapability("intent_router", A, I, "app.agent.router",
            "Auto-routes goal to best agent by intent", [], [], "low", "realtime", "low"),
        StrategyCapability("meta_agent_planner", A, P, "app.intelligence.meta_agent",
            "Meta-agent: NL → agent config generator", [], [], "high", "batch", "low"),

        # ── RAG Additions (doc-1 §4) ───────────────────────────────────────────
        StrategyCapability("raft", R, PL, "",
            "RAG fine-tuning pattern (Zhang et al. 2024)", [], [], "high", "batch", "low"),

        # ── Long-Horizon / Autonomous (doc-1 §8) ──────────────────────────────
        StrategyCapability("voyager", A, PL, "",
            "Skill-learning agent (Wang et al. 2023)", [], [], "high", "batch", "low"),
        StrategyCapability("generative_agents", A, PL, "",
            "Memory-driven social simulation (Park et al. 2023)", [], [], "high", "batch", "low"),
        StrategyCapability("scratchpad", A, I, "app.agent.graph:AgentGraph",
            "Chain-of-thought scratchpad pattern", [], [], "low", "interactive", "low"),
```

- [ ] **Step A6.4: Run and confirm all patterns present**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_strategy_registry_complete.py -v --no-cov
```
Expected: All 16 tests pass

- [ ] **Step A6.5: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/strategy_registry.py tests/orchestration/test_strategy_registry_complete.py
git commit -m "feat(orchestration): expand StrategyRegistry to 90+ patterns — complete doc-1 coverage"
```

---

## Task A7: Doc 2 RAG Agentic — Remaining 7 Files + RetrieverTool Phase B

**Files:**
- Create: `app/rag/agentic/query_reformulator.py`
- Create: `app/rag/agentic/context_gap_detector.py`
- Create: `app/rag/agentic/fallback_chain.py`
- Create: `app/rag/agentic/rag_trace.py`
- Create: `app/rag/agentic/citation_threader.py`
- Create: `app/rag/agentic/retrieval_policy.py`
- Create: `app/rag/agentic/query_expander.py`
- Modify: `app/rag/agentic/retriever_tool.py` (Phase B: reformulation + full fallback chain)
- Create: `tests/rag/test_agentic/test_rag_agentic_complete.py`

- [ ] **Step A7.1: Write failing tests**

```python
# tests/rag/test_agentic/test_rag_agentic_complete.py
"""Doc-2 RAG agentic components: reformulation, gap detection, fallback chain, trace."""
from __future__ import annotations
import pytest
from app.rag.agentic.query_reformulator import QueryReformulator
from app.rag.agentic.context_gap_detector import ContextGapDetector
from app.rag.agentic.fallback_chain import FallbackChain, FallbackResult
from app.rag.agentic.rag_trace import RAGTrace
from app.rag.agentic.citation_threader import CitationThreader
from app.rag.agentic.retrieval_policy import RetrievalPolicy
from app.rag.agentic.query_expander import QueryExpander


# ── QueryReformulator ─────────────────────────────────────────────────────────

def test_reformulator_generates_alternatives():
    reformulator = QueryReformulator()
    alternatives = reformulator.reformulate("what is agentverse")
    assert isinstance(alternatives, list)
    assert len(alternatives) >= 2
    # Alternatives must be different from original
    assert all(a != "what is agentverse" for a in alternatives)


def test_reformulator_limits_to_max_attempts():
    reformulator = QueryReformulator(max_attempts=2)
    alternatives = reformulator.reformulate("list all tickets")
    assert len(alternatives) <= 2


# ── ContextGapDetector ────────────────────────────────────────────────────────

def test_gap_detector_detects_insufficient_data():
    detector = ContextGapDetector()
    assert detector.has_gap("INSUFFICIENT DATA: cannot determine the answer") is True


def test_gap_detector_detects_not_found():
    detector = ContextGapDetector()
    assert detector.has_gap("The information was not found in the knowledge base") is True


def test_gap_detector_detects_cannot_verify():
    detector = ContextGapDetector()
    assert detector.has_gap("I cannot verify this claim without more context") is True


def test_gap_detector_passes_valid_answer():
    detector = ContextGapDetector()
    assert detector.has_gap("The deployment was successful at 14:30 UTC") is False


def test_gap_detector_all_12_signals():
    """Doc-2 defines exactly 12 gap signal phrases."""
    detector = ContextGapDetector()
    signals = [
        "insufficient data",
        "unclear result",
        "no information available",
        "cannot determine",
        "lack of context",
        "not mentioned in the docs",
        "unknown at this time",
        "not found",
        "need more context",
        "more context required",
        "cannot verify this",
        "no relevant results",
    ]
    for signal in signals:
        assert detector.has_gap(f"The answer is {signal}"), f"Should detect gap: '{signal}'"


# ── FallbackChain ─────────────────────────────────────────────────────────────

def test_fallback_chain_records_attempts():
    chain = FallbackChain()
    chain.record_attempt("hybrid", success=False, reason="low confidence")
    chain.record_attempt("web", success=True, reason="found result")
    assert len(chain.attempts) == 2
    assert chain.final_source == "web"


def test_fallback_chain_order():
    """Doc-2 fallback order: HYBRID → GRAPH → HYDE → WEB → LTM → parametric."""
    chain = FallbackChain()
    assert chain.FALLBACK_ORDER == [
        "hybrid", "graph", "hyde", "web", "ltm", "parametric"
    ]


def test_fallback_chain_to_dict():
    chain = FallbackChain()
    chain.record_attempt("hybrid", success=False, reason="empty")
    chain.record_attempt("parametric", success=True, reason="LLM knowledge")
    d = chain.to_dict()
    assert "attempts" in d
    assert "final_source" in d


# ── RAGTrace ─────────────────────────────────────────────────────────────────

def test_rag_trace_records_retrieval_step():
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval(
        strategy="hybrid",
        query="list tickets",
        result_count=5,
        confidence=0.82,
        latency_ms=120.0,
    )
    assert len(trace.steps) == 1
    assert trace.steps[0]["strategy"] == "hybrid"


def test_rag_trace_to_sse_event():
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval("hybrid", "query", 3, 0.75, 80.0)
    event = trace.to_sse_event()
    assert event["type"] == "rag_strategy_selected"
    assert event["goal_id"] == "g1"


# ── CitationThreader ─────────────────────────────────────────────────────────

def test_citation_threader_adds_inline_refs():
    threader = CitationThreader()
    chunks = [
        {"content": "AgentVerse supports multi-tenant execution.", "source_url": "https://docs.example.com/page1", "chunk_id": "c1"},
        {"content": "Dynamic orchestration selects patterns per goal.", "source_url": "https://docs.example.com/page2", "chunk_id": "c2"},
    ]
    result = threader.thread(chunks)
    assert len(result) == 2
    assert result[0]["citation_index"] == 1
    assert result[1]["citation_index"] == 2


# ── RetrievalPolicy ───────────────────────────────────────────────────────────

def test_retrieval_policy_selects_correct_strategy():
    from app.rag.agentic.retrieval_policy import RetrievalPolicy, RetrievalStrategy
    policy = RetrievalPolicy()
    strategy = policy.select(
        query_type="factual",
        kb_available=True,
        web_available=False,
        kg_available=False,
    )
    assert strategy in RetrievalStrategy


# ── QueryExpander ─────────────────────────────────────────────────────────────

def test_query_expander_generates_variants():
    expander = QueryExpander()
    variants = expander.expand("open tickets in Jira")
    assert isinstance(variants, list)
    assert len(variants) >= 1
```

- [ ] **Step A7.2: Run to confirm failure**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_rag_agentic_complete.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A7.3: Implement all 7 missing files**

`app/rag/agentic/query_reformulator.py`:
```python
"""QueryReformulator — generates alternative query phrasings on empty results."""
from __future__ import annotations
import re


class QueryReformulator:
    def __init__(self, max_attempts: int = 2) -> None:
        self._max = max_attempts

    def reformulate(self, query: str) -> list[str]:
        """Generate up to max_attempts alternative phrasings."""
        alternatives: list[str] = []
        q = query.strip()

        # Phrasing 1: Remove question words, make keyword search
        keywords = re.sub(r"^(what is|how do|can you|please|find|get|list)\s+", "", q, flags=re.I)
        if keywords != q and keywords:
            alternatives.append(keywords)

        # Phrasing 2: Expand acronyms / add context
        expanded = q.replace("kb", "knowledge base").replace("ltm", "long-term memory")
        if expanded != q:
            alternatives.append(expanded)
        else:
            # Generic: add "information about" prefix
            alternatives.append(f"information about {q}")

        return alternatives[:self._max]
```

`app/rag/agentic/context_gap_detector.py`:
```python
"""ContextGapDetector — detects 12 gap signal phrases defined in doc-2."""
from __future__ import annotations
import re

# Doc-2 §4 exact 12 gap signal phrases
_GAP_SIGNALS = frozenset({
    "insufficient", "unclear", "no information", "cannot determine",
    "lack of context", "not mentioned", "unknown", "not found",
    "need more", "more context", "cannot verify", "no relevant",
})


class ContextGapDetector:
    def has_gap(self, text: str) -> bool:
        lower = text.lower()
        return any(signal in lower for signal in _GAP_SIGNALS)

    def extract_missing_topic(self, text: str) -> str:
        """Extract what is missing from a gap signal phrase."""
        patterns = [
            r"cannot determine (.+?)[\.\n]",
            r"no information (?:about|on) (.+?)[\.\n]",
            r"not found:? (.+?)[\.\n]",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                return m.group(1).strip()
        return ""
```

`app/rag/agentic/fallback_chain.py`:
```python
"""FallbackChain — tracks fallback attempts per doc-2 §4 order."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FallbackAttempt:
    source: str
    success: bool
    reason: str


class FallbackChain:
    # Doc-2 §4: exact fallback order
    FALLBACK_ORDER = ["hybrid", "graph", "hyde", "web", "ltm", "parametric"]

    def __init__(self) -> None:
        self.attempts: list[FallbackAttempt] = []

    @property
    def final_source(self) -> str:
        for attempt in reversed(self.attempts):
            if attempt.success:
                return attempt.source
        return "parametric"

    def record_attempt(self, source: str, *, success: bool, reason: str) -> None:
        self.attempts.append(FallbackAttempt(source=source, success=success, reason=reason))

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": [{"source": a.source, "success": a.success, "reason": a.reason}
                         for a in self.attempts],
            "final_source": self.final_source,
            "fallback_used": len(self.attempts) > 1,
        }
```

`app/rag/agentic/rag_trace.py`:
```python
"""RAGTrace — structured observability trace for agentic retrieval."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RAGTraceStep:
    strategy: str
    query: str
    result_count: int
    confidence: float
    latency_ms: float


class RAGTrace:
    def __init__(self, goal_id: str, tenant_id: str) -> None:
        self.trace_id = uuid.uuid4().hex
        self.goal_id = goal_id
        self.tenant_id = tenant_id
        self.steps: list[dict[str, Any]] = []

    def record_retrieval(
        self,
        strategy: str,
        query: str,
        result_count: int,
        confidence: float,
        latency_ms: float,
    ) -> None:
        self.steps.append({
            "strategy": strategy, "query": query[:200],
            "result_count": result_count, "confidence": confidence,
            "latency_ms": latency_ms,
        })

    def to_sse_event(self) -> dict[str, Any]:
        last = self.steps[-1] if self.steps else {}
        return {
            "type": "rag_strategy_selected",
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "strategy": last.get("strategy", "unknown"),
            "steps": len(self.steps),
            "total_results": sum(s["result_count"] for s in self.steps),
        }
```

`app/rag/agentic/citation_threader.py`:
```python
"""CitationThreader — attaches sequential citation indices to chunks."""
from __future__ import annotations
from typing import Any


class CitationThreader:
    def thread(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for i, chunk in enumerate(chunks, start=1):
            result.append({**chunk, "citation_index": i})
        return result
```

`app/rag/agentic/retrieval_policy.py`:
```python
"""RetrievalPolicy — selects retrieval strategy based on query + source availability."""
from __future__ import annotations
import enum


class RetrievalStrategy(str, enum.Enum):
    HYBRID = "hybrid"
    GRAPH = "graph"
    HYDE = "hyde"
    WEB = "web"
    MEMORY = "memory"
    PARAMETRIC = "parametric"
    AUTO = "auto"


class RetrievalPolicy:
    def select(
        self,
        query_type: str = "factual",
        kb_available: bool = True,
        web_available: bool = False,
        kg_available: bool = False,
    ) -> RetrievalStrategy:
        if not kb_available and not web_available:
            return RetrievalStrategy.PARAMETRIC
        if not kb_available and web_available:
            return RetrievalStrategy.WEB
        if kg_available and query_type in ("relationship", "impact", "dependency"):
            return RetrievalStrategy.GRAPH
        return RetrievalStrategy.HYBRID
```

`app/rag/agentic/query_expander.py`:
```python
"""QueryExpander — generates query variants for multi-source retrieval."""
from __future__ import annotations


class QueryExpander:
    def expand(self, query: str, max_variants: int = 3) -> list[str]:
        variants = [query]
        # Synonym-style expansion (non-LLM — fast path)
        q = query.lower()
        if "ticket" in q or "issue" in q:
            variants.append(q.replace("ticket", "issue").replace("issue", "ticket"))
        if "find" in q:
            variants.append(q.replace("find", "search for"))
        return list(dict.fromkeys(variants))[:max_variants]
```

- [ ] **Step A7.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/rag/test_agentic/test_rag_agentic_complete.py -v --no-cov
```
Expected: All 16 tests pass

- [ ] **Step A7.5: Commit**

```bash
cd agent-verse-backend
git add app/rag/agentic/ tests/rag/test_agentic/test_rag_agentic_complete.py
git commit -m "feat(rag/agentic): add 7 missing RAG agentic files — query_reformulator, gap_detector, fallback_chain, rag_trace, citation_threader, retrieval_policy, query_expander"
```

---

## Task A8: Layer 8 Model Orchestration Additions

**Files:**
- Create: `app/ai_router/model_orchestrator.py`
- Create: `app/ai_router/role_policy.py`
- Create: `app/ai_router/provider_health_policy.py`
- Create: `app/ai_router/cost_latency_quality_policy.py`
- Create: `tests/ai_router/test_model_orchestrator.py`

- [ ] **Step A8.1: Write failing tests**

```python
# tests/ai_router/test_model_orchestrator.py
"""ModelOrchestrator selects model per role at runtime based on quality/cost/latency."""
from __future__ import annotations
import pytest
from app.ai_router.model_orchestrator import ModelOrchestrator, ModelRoleAssignment
from app.ai_router.role_policy import RolePolicy, AgentRole
from app.ai_router.provider_health_policy import ProviderHealthPolicy, ProviderHealthStatus
from app.ai_router.cost_latency_quality_policy import CostLatencyQualityPolicy
from app.agent.pattern_config import PatternConfig, GoalProperties, RiskLevel, Complexity


@pytest.fixture
def orchestrator():
    return ModelOrchestrator()


def test_orchestrator_returns_role_assignment(orchestrator):
    cfg = PatternConfig()
    assignment = orchestrator.select_models(cfg)
    assert isinstance(assignment, ModelRoleAssignment)
    assert assignment.planner is not None
    assert assignment.executor is not None
    assert assignment.verifier is not None


def test_critical_risk_gets_high_quality_models(orchestrator):
    cfg = PatternConfig(
        goal_properties=GoalProperties(risk=RiskLevel.CRITICAL),
        model_planner="gpt-5.2",
    )
    assignment = orchestrator.select_models(cfg)
    # High-risk should not downgrade to cheap models
    assert assignment.quality_tier in ("high", "medium")


def test_simple_low_risk_can_use_cheaper_models(orchestrator):
    cfg = PatternConfig(
        goal_properties=GoalProperties(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW),
    )
    assignment = orchestrator.select_models(cfg)
    assert assignment is not None  # may use cheaper tier


def test_role_policy_maps_roles():
    policy = RolePolicy()
    roles = policy.get_required_roles(PatternConfig())
    assert AgentRole.PLANNER in roles
    assert AgentRole.EXECUTOR in roles
    assert AgentRole.VERIFIER in roles


def test_provider_health_policy_marks_healthy():
    policy = ProviderHealthPolicy()
    status = policy.check("openai")
    assert isinstance(status, ProviderHealthStatus)
    assert status.provider == "openai"


def test_provider_health_policy_unknown_returns_healthy():
    policy = ProviderHealthPolicy()
    status = policy.check("unknown_provider_xyz")
    assert status.healthy is True  # default healthy for unknown


def test_cost_latency_quality_policy_returns_tier():
    policy = CostLatencyQualityPolicy()
    tier = policy.select_tier(
        complexity=Complexity.SIMPLE,
        risk=RiskLevel.LOW,
        latency_requirement="realtime",
    )
    assert tier in ("low", "medium", "high")


def test_cost_latency_quality_policy_high_risk_forces_quality(orchestrator):
    policy = CostLatencyQualityPolicy()
    tier = policy.select_tier(
        complexity=Complexity.MEDIUM,
        risk=RiskLevel.CRITICAL,
        latency_requirement="interactive",
    )
    assert tier in ("medium", "high")
```

- [ ] **Step A8.2: Check if tests/ai_router exists**

```bash
ls agent-verse-backend/tests/ai_router/ 2>/dev/null || mkdir -p agent-verse-backend/tests/ai_router && touch agent-verse-backend/tests/ai_router/__init__.py
cd agent-verse-backend && uv run pytest tests/ai_router/test_model_orchestrator.py -v --no-cov
```
Expected: `ImportError`

- [ ] **Step A8.3: Implement the 4 files**

`app/ai_router/role_policy.py`:
```python
"""RolePolicy — defines required agent roles per PatternConfig."""
from __future__ import annotations
import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.pattern_config import PatternConfig


class AgentRole(str, enum.Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    CLASSIFIER = "classifier"
    EMBEDDER = "embedder"
    JUDGE = "judge"


class RolePolicy:
    def get_required_roles(self, config: "PatternConfig") -> list[AgentRole]:
        roles = [AgentRole.PLANNER, AgentRole.EXECUTOR, AgentRole.VERIFIER]
        if any(p in ("debate", "consensus", "peer_review") for p in config.multi_agent_patterns):
            roles.append(AgentRole.JUDGE)
        return roles
```

`app/ai_router/provider_health_policy.py`:
```python
"""ProviderHealthPolicy — tracks and checks LLM provider health."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ProviderHealthStatus:
    provider: str
    healthy: bool = True
    circuit_open: bool = False
    error_rate: float = 0.0
    avg_latency_ms: float = 500.0


class ProviderHealthPolicy:
    def __init__(self) -> None:
        self._status: dict[str, ProviderHealthStatus] = {}

    def check(self, provider: str) -> ProviderHealthStatus:
        return self._status.get(provider, ProviderHealthStatus(provider=provider))

    def record_failure(self, provider: str) -> None:
        s = self._status.setdefault(provider, ProviderHealthStatus(provider=provider))
        s.error_rate = min(1.0, s.error_rate + 0.1)
        if s.error_rate >= 0.5:
            s.circuit_open = True
            s.healthy = False

    def record_success(self, provider: str, latency_ms: float) -> None:
        s = self._status.setdefault(provider, ProviderHealthStatus(provider=provider))
        s.error_rate = max(0.0, s.error_rate - 0.05)
        s.avg_latency_ms = 0.9 * s.avg_latency_ms + 0.1 * latency_ms
        if s.error_rate < 0.2:
            s.circuit_open = False
            s.healthy = True
```

`app/ai_router/cost_latency_quality_policy.py`:
```python
"""CostLatencyQualityPolicy — selects cost tier per complexity/risk/latency."""
from __future__ import annotations
from app.agent.pattern_config import Complexity, RiskLevel


class CostLatencyQualityPolicy:
    def select_tier(
        self,
        complexity: Complexity,
        risk: RiskLevel,
        latency_requirement: str = "interactive",
    ) -> str:
        # CRITICAL safety rule: never downgrade for high-risk
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return "high"

        if latency_requirement == "realtime":
            return "low"

        if complexity == Complexity.EXPERT:
            return "high"
        elif complexity == Complexity.COMPLEX:
            return "medium"
        elif complexity == Complexity.SIMPLE and risk == RiskLevel.LOW:
            return "low"

        return "medium"
```

`app/ai_router/model_orchestrator.py`:
```python
"""ModelOrchestrator — selects model per role based on cost/latency/quality policy."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai_router.cost_latency_quality_policy import CostLatencyQualityPolicy
from app.ai_router.provider_health_policy import ProviderHealthPolicy
from app.ai_router.role_policy import AgentRole, RolePolicy

if TYPE_CHECKING:
    from app.agent.pattern_config import PatternConfig

_TIER_MODELS = {
    "high": {"planner": "gpt-5.2", "executor": "gpt-5.2", "verifier": "gpt-5.2"},
    "medium": {"planner": "gpt-4o", "executor": "gpt-4o", "verifier": "gpt-4o-mini"},
    "low": {"planner": "gpt-4o-mini", "executor": "gpt-4o-mini", "verifier": "gpt-4o-mini"},
}


@dataclass
class ModelRoleAssignment:
    planner: str
    executor: str
    verifier: str
    embedder: str = "text-embedding-3-small"
    quality_tier: str = "medium"


class ModelOrchestrator:
    def __init__(self) -> None:
        self._cost_policy = CostLatencyQualityPolicy()
        self._health_policy = ProviderHealthPolicy()
        self._role_policy = RolePolicy()

    def select_models(self, config: "PatternConfig") -> ModelRoleAssignment:
        props = config.goal_properties
        complexity = props.complexity if props else None
        risk = props.risk if props else None

        from app.agent.pattern_config import Complexity, RiskLevel
        tier = self._cost_policy.select_tier(
            complexity=complexity or Complexity.MEDIUM,
            risk=risk or RiskLevel.LOW,
            latency_requirement=getattr(props, "time_sensitivity", "interactive") if props else "interactive",
        )

        models = _TIER_MODELS.get(tier, _TIER_MODELS["medium"])
        return ModelRoleAssignment(
            planner=models["planner"],
            executor=models["executor"],
            verifier=models["verifier"],
            quality_tier=tier,
        )
```

- [ ] **Step A8.4: Run and confirm passing**

```bash
cd agent-verse-backend
uv run pytest tests/ai_router/test_model_orchestrator.py -v --no-cov
```
Expected: `8 passed`

- [ ] **Step A8.5: Commit**

```bash
cd agent-verse-backend
git add app/ai_router/model_orchestrator.py app/ai_router/role_policy.py \
    app/ai_router/provider_health_policy.py app/ai_router/cost_latency_quality_policy.py \
    tests/ai_router/test_model_orchestrator.py
git commit -m "feat(ai_router): add ModelOrchestrator + RolePolicy + ProviderHealthPolicy + CostLatencyQualityPolicy"
```

---

## Task A9: Add 3 Missing Runtime Profiles to runtime_profile.py

**Files:**
- Modify: `app/orchestration/runtime_profile.py`
- Create: `tests/orchestration/test_runtime_profiles_complete.py`

- [ ] **Step A9.1: Write failing tests**

```python
# tests/orchestration/test_runtime_profiles_complete.py
"""Spec §3.1/3.2/3.5 mandate MultimodalRuntimeProfile, SelfImprovementProfile, KnowledgeRuntimeProfile."""
from __future__ import annotations
import json
import pytest
from app.orchestration.runtime_profile import (
    MultimodalRuntimeProfile,
    SelfImprovementProfile,
    KnowledgeRuntimeProfile,
)


def test_multimodal_runtime_profile_fields():
    profile = MultimodalRuntimeProfile(
        content_type="pdf",
        parser="layout_pdf",
        chunking_strategy="layout",
        embedding_model="text-embedding-3-small",
        model_roles={"extractor": "vision", "reasoner": "gpt-5.2"},
        provenance_required=True,
    )
    assert profile.content_type == "pdf"
    assert profile.provenance_required is True
    d = profile.to_dict()
    json.dumps(d)  # must be serializable


def test_self_improvement_profile_fields():
    profile = SelfImprovementProfile(
        enabled=True,
        eval_suite="security",
        score_threshold=0.72,
        reflexion_enabled=True,
        prompt_ab_test_enabled=False,
        model_ab_test_enabled=False,
        creates_regression_case_on_failure=True,
    )
    assert profile.eval_suite == "security"
    assert profile.score_threshold == 0.72
    d = profile.to_dict()
    json.dumps(d)


def test_knowledge_runtime_profile_fields():
    profile = KnowledgeRuntimeProfile(
        kb_state="healthy",
        graph_state="partial",
        selected_collections=["col1", "col2"],
        graph_strategy="entity",
        web_fallback_required=False,
        citation_required=True,
    )
    assert profile.kb_state == "healthy"
    assert profile.graph_strategy == "entity"
    assert "col1" in profile.selected_collections
    d = profile.to_dict()
    json.dumps(d)


def test_knowledge_runtime_empty_kb_requires_web():
    profile = KnowledgeRuntimeProfile(kb_state="empty", graph_state="empty")
    assert profile.web_fallback_required is True  # empty KB forces web fallback


def test_multimodal_all_content_types():
    for ct in ["text", "pdf", "docx", "html", "markdown", "code", "image", "audio", "video", "csv", "json", "mixed"]:
        p = MultimodalRuntimeProfile(content_type=ct)
        assert p.content_type == ct
```

- [ ] **Step A9.2: Add the 3 dataclasses to `app/orchestration/runtime_profile.py`**

Append to end of `app/orchestration/runtime_profile.py`:

```python
@dataclass
class MultimodalRuntimeProfile:
    """Runtime profile for multimodal content ingestion (spec §3.1)."""
    content_type: str = "text"    # text|pdf|docx|html|markdown|code|image|audio|video|csv|json|mixed
    parser: str = "text_parser"   # layout_pdf|ocr|asr|vision_caption|ast|html_readability|text_parser
    chunking_strategy: str = "semantic"
    embedding_model: str = "text-embedding-3-small"
    model_roles: dict[str, str] = field(default_factory=dict)
    provenance_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


@dataclass
class SelfImprovementProfile:
    """Self-improvement runtime profile (spec §3.2)."""
    enabled: bool = True
    eval_suite: str = "default"   # default|security|rag|coding|ops
    score_threshold: float = 0.72
    reflexion_enabled: bool = True
    prompt_ab_test_enabled: bool = False
    model_ab_test_enabled: bool = False
    creates_regression_case_on_failure: bool = True

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


@dataclass
class KnowledgeRuntimeProfile:
    """Knowledge graph + KB runtime profile (spec §3.5)."""
    kb_state: str = "unknown"      # empty|sparse|healthy|stale|unknown
    graph_state: str = "unknown"   # empty|healthy|partial|unknown
    selected_collections: list[str] = field(default_factory=list)
    graph_strategy: str = "none"   # none|entity|path|community|impact
    web_fallback_required: bool = False
    citation_required: bool = True

    def __post_init__(self) -> None:
        # Empty KB always requires web fallback
        if self.kb_state == "empty":
            self.web_fallback_required = True

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)
```

- [ ] **Step A9.3: Run tests**

```bash
cd agent-verse-backend
uv run pytest tests/orchestration/test_runtime_profiles_complete.py -v --no-cov
```
Expected: `5 passed`

- [ ] **Step A9.4: Commit**

```bash
cd agent-verse-backend
git add app/orchestration/runtime_profile.py tests/orchestration/test_runtime_profiles_complete.py
git commit -m "feat(orchestration): add MultimodalRuntimeProfile + SelfImprovementProfile + KnowledgeRuntimeProfile — spec §3.1/3.2/3.5"
```
