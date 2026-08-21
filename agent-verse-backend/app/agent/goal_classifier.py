"""GoalClassifier — two-tier goal classification (doc-4 exact implementation).

Tier 1: Fast keyword-based (<1ms) — always runs.
Tier 2: LLM-based (~200ms) — only for MEDIUM complexity + confidence <= 0.85.
"""

from __future__ import annotations

import re
from typing import Any

from app.agent.pattern_config import Complexity, Domain, GoalProperties, RiskLevel

# Exact keyword sets from doc-4
_RISK_KEYWORDS = frozenset(
    {
        "delete",
        "drop",
        "truncate",
        "destroy",
        "wipe",
        "purge",
        "rm -rf",
        "deploy",
        "production",
        "prod",
        "overwrite",
        "payment",
        "charge",
        "billing",
        "transfer funds",
        "admin",
        "sudo",
        "root access",
        "send email",
        "send sms",
        "post to",
        "publish",
        "release",
    }
)

_COMPLEXITY_SIGNALS: dict[str, list[str]] = {
    "expert": [
        "design",
        "architect",
        "optimise",
        "analyse",
        "analyze",
        "evaluate",
        "compare",
        "strategy",
        "tradeoff",
        "tradeoffs",
        "distributed system",
        "security audit",
        "performance",
        "scalability",
    ],
    "complex": [
        "explain",
        "how does",
        "why",
        "implement",
        "create",
        "build",
        "write code",
        "research",
        "investigate",
        "integrate",
    ],
    "simple": [
        "list",
        "show",
        "get",
        "fetch",
        "what is",
        "how many",
        "count",
        "status",
        "check",
        "ping",
        "find",
    ],
}

_DOMAIN_SIGNALS: dict[str, list[str]] = {
    "technical": [
        "code",
        "api",
        "database",
        "server",
        "deploy",
        "debug",
        "test",
        "sql",
        "python",
        "javascript",
        "docker",
        "kubernetes",
        "git",
    ],
    "creative": [
        "write",
        "generate",
        "draft",
        "story",
        "poem",
        "design",
        "create",
        "compose",
        "brainstorm",
    ],
    "analytical": [
        "analyse",
        "analyze",
        "evaluate",
        "compare",
        "research",
        "explain",
        "why",
        "tradeoff",
        "performance",
        "metrics",
        "data",
    ],
    "operational": [
        "deploy",
        "monitor",
        "alert",
        "backup",
        "scale",
        "migrate",
        "operate",
        "run",
        "restart",
    ],
}

_WEB_SIGNALS = frozenset(
    {
        "latest",
        "current",
        "recent",
        "today",
        "news",
        "price",
        "version",
        "now",
        "2024",
        "2025",
        "2026",
        "live",
    }
)

_IRREVERSIBLE_SIGNALS = frozenset(
    {
        "delete",
        "drop",
        "truncate",
        "destroy",
        "wipe",
        "purge",
        "send email",
        "send sms",
        "post",
        "publish",
        "release",
        "deploy",
        "payment",
        "transfer",
        "charge",
    }
)

_CLASSIFIER_SYSTEM = """\
You are a goal complexity classifier. Analyze the given goal and classify it.
Return ONLY valid JSON — no markdown, no explanation:
{"complexity":"simple|medium|complex|expert","domain":"technical|creative|analytical|operational|conversational","risk":"low|medium|high|critical","time_sensitivity":"realtime|normal|batch","knowledge_requirement":"none|kb_only|web_required|expert_domain","reversibility":"reversible|irreversible","requires_web":true|false,"estimated_steps":1-10,"confidence":0.0-1.0}
"""


def _phrase_in(phrase: str, lower: str, tokens: set[str]) -> bool:
    """Word-boundary-safe phrase membership test."""
    if " " in phrase:
        return phrase in lower
    return phrase in tokens


class GoalClassifier:
    """Two-tier goal classifier."""

    def classify_fast(self, goal: str) -> GoalProperties:
        """Tier 1: keyword-based, always < 1ms."""
        lower = goal.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))

        # Risk
        risk = RiskLevel.LOW
        reversibility = "reversible"
        for phrase in _RISK_KEYWORDS:
            if _phrase_in(phrase, lower, tokens):
                if phrase in (
                    "delete",
                    "drop",
                    "truncate",
                    "destroy",
                    "wipe",
                    "purge",
                    "payment",
                    "charge",
                    "billing",
                    "transfer funds",
                ):
                    risk = RiskLevel.CRITICAL
                else:
                    if risk.value in ("low", "medium"):
                        risk = RiskLevel.HIGH
                break
        if risk == RiskLevel.LOW:
            for phrase in ("deploy", "publish", "release", "send email", "send sms"):
                if _phrase_in(phrase, lower, tokens):
                    risk = RiskLevel.HIGH
                    break
        for phrase in _IRREVERSIBLE_SIGNALS:
            if _phrase_in(phrase, lower, tokens):
                reversibility = "irreversible"
                break

        # Web / realtime
        requires_web = bool(tokens & _WEB_SIGNALS) or any(p in lower for p in _WEB_SIGNALS)
        time_sensitivity = "realtime" if requires_web else "normal"
        knowledge_requirement = "web_required" if requires_web else "kb_only"

        # Complexity
        expert_hits = sum(1 for s in _COMPLEXITY_SIGNALS["expert"] if s in lower)
        complex_hits = sum(1 for s in _COMPLEXITY_SIGNALS["complex"] if s in lower)
        simple_hits = sum(1 for s in _COMPLEXITY_SIGNALS["simple"] if s in lower)
        step_count = (
            len(re.findall(r"\band\b|\bthen\b|\bafter\b|\bfollowed by\b|\balso\b", lower)) + 1
        )

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

        # Domain
        domain_scores = {
            Domain.TECHNICAL: sum(1 for s in _DOMAIN_SIGNALS["technical"] if s in lower),
            Domain.CREATIVE: sum(1 for s in _DOMAIN_SIGNALS["creative"] if s in lower),
            Domain.ANALYTICAL: sum(1 for s in _DOMAIN_SIGNALS["analytical"] if s in lower),
            Domain.OPERATIONAL: sum(1 for s in _DOMAIN_SIGNALS["operational"] if s in lower),
        }
        domain = max(domain_scores, key=lambda d: domain_scores[d])
        if domain_scores[domain] == 0:
            domain = Domain.OPERATIONAL

        # Confidence
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

    async def classify_with_llm(
        self,
        goal: str,
        provider: Any,
        fast_props: GoalProperties | None = None,
    ) -> GoalProperties:
        """Tier 2: LLM-assisted for MEDIUM complexity + confidence <= 0.85."""
        base = fast_props or self.classify_fast(goal)
        if provider is None:
            return base
        if base.confidence > 0.85 or base.complexity != Complexity.MEDIUM:
            return base
        try:
            import json

            from app.providers.base import CompletionRequest, Message

            resp = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_CLASSIFIER_SYSTEM),
                        Message(role="user", content=f"Goal: {goal[:500]}"),
                    ],
                    model="",
                    max_tokens=150,
                    temperature=0.0,
                )
            )
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
