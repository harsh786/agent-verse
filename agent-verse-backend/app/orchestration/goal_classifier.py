"""GoalClassifier — two-tier classification of incoming goals.
Tier 1: Fast keyword-based (<1ms) — always runs.
Tier 2: LLM-based (~200ms) — runs only for MEDIUM complexity when confidence <= 0.85.
"""
from __future__ import annotations

import re
from typing import Any

from app.orchestration.runtime_profile import (
    Complexity,
    Domain,
    GoalProperties,
    KnowledgeState,
    RiskLevel,
    TimeSensitivity,
)

_CRITICAL_RISK = frozenset({
    "delete",
    "drop",
    "truncate",
    "destroy",
    "wipe",
    "purge",
    "rm -rf",
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
    "send email blast",
    "publish release",
})
_HIGH_RISK = frozenset({
    "deploy",
    "migrate",
    "alter table",
    "modify schema",
    "send email",
    "send sms",
    "post to",
    "release",
    "revoke",
    "terminate",
    "disable account",
    "reset password",
    "grant admin",
})
_IRREVERSIBLE = frozenset({
    "delete",
    "drop",
    "truncate",
    "destroy",
    "wipe",
    "purge",
    "payment",
    "transfer",
    "charge",
    "send email",
    "publish",
    "release",
})
_COMPLEXITY_EXPERT = frozenset({
    "analyze",
    "research",
    "compare",
    "synthesize",
    "evaluate",
    "comprehensive",
    "strategic",
    "multi-step",
    "cross-reference",
    "architecture",
    "design",
    "forecast",
    "model",
})
_COMPLEXITY_COMPLEX = frozenset({
    "report",
    "summary",
    "breakdown",
    "multiple",
    "then",
    "after",
    "also",
    "and then",
    "followed by",
    "step by step",
})
_STEP_SEPARATORS = re.compile(
    r"\band\b|\bthen\b|\bafter\b|\bfollowed by\b|\balso\b|\bnext\b", re.IGNORECASE
)
_WEB_SIGNALS = frozenset({
    "latest",
    "current",
    "recent",
    "today",
    "news",
    "price",
    "version",
    "now",
    "2025",
    "2026",
    "live",
    "real-time",
    "right now",
})
_TECHNICAL_SIGNALS = frozenset({
    "code",
    "function",
    "api",
    "database",
    "sql",
    "python",
    "javascript",
    "docker",
    "kubernetes",
    "git",
    "github",
    "aws",
    "gcp",
    "azure",
    "bug",
    "error",
    "deploy",
    "test",
    "script",
    "query",
    "schema",
})
_ANALYTICAL_SIGNALS = frozenset({
    "analyze",
    "analysis",
    "data",
    "metrics",
    "statistics",
    "forecast",
    "trend",
    "compare",
    "correlation",
    "regression",
    "model",
    "chart",
    "dashboard",
    "report",
    "kpi",
})
_CREATIVE_SIGNALS = frozenset({
    "write",
    "create",
    "draft",
    "generate",
    "compose",
    "design",
    "brainstorm",
    "ideate",
    "story",
    "blog",
    "email",
    "proposal",
})


def _phrase_in(phrase: str, lower: str, tokens: set[str]) -> bool:
    """Word-boundary-safe phrase membership test.
    Single-word phrases check the pre-tokenised set (word boundaries enforced).
    Multi-word phrases use substring (spaces prevent word-boundary collisions).
    """
    if " " in phrase:
        return phrase in lower
    return phrase in tokens


class GoalClassifier:
    def classify_fast(self, goal: str) -> GoalProperties:
        lower = goal.lower()
        tokens = set(re.findall(r"\b\w+\b", lower))

        risk = RiskLevel.LOW
        reversibility = "reversible"
        for phrase in _CRITICAL_RISK:
            if _phrase_in(phrase, lower, tokens):
                risk = RiskLevel.CRITICAL
                reversibility = "irreversible"
                break
        if risk == RiskLevel.LOW:
            for phrase in _HIGH_RISK:
                if _phrase_in(phrase, lower, tokens):
                    risk = RiskLevel.HIGH
                    break
        for phrase in _IRREVERSIBLE:
            if _phrase_in(phrase, lower, tokens):
                reversibility = "irreversible"
                break

        requires_web = bool(tokens & _WEB_SIGNALS) or any(p in lower for p in _WEB_SIGNALS)
        time_sensitivity = TimeSensitivity.REALTIME if requires_web else TimeSensitivity.NORMAL
        requires_code = bool(
            tokens & {"code", "function", "script", "python", "javascript", "sql", "query", "test"}
        )

        step_count = len(_STEP_SEPARATORS.findall(goal)) + 1
        expert_hits = len(tokens & _COMPLEXITY_EXPERT)
        complex_hits = len(tokens & _COMPLEXITY_COMPLEX)

        if expert_hits >= 2 or step_count >= 5:
            complexity = Complexity.EXPERT
            estimated_steps = max(5, step_count)
        elif expert_hits >= 1 or complex_hits >= 2 or step_count >= 3:
            complexity = Complexity.COMPLEX
            estimated_steps = max(4, step_count)
        elif step_count >= 2 or complex_hits >= 1:
            complexity = Complexity.MEDIUM
            estimated_steps = max(2, step_count)
        else:
            complexity = Complexity.SIMPLE
            estimated_steps = 2

        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) and complexity == Complexity.SIMPLE:
            complexity = Complexity.MEDIUM
            estimated_steps = max(3, estimated_steps)

        tech_score = len(tokens & _TECHNICAL_SIGNALS)
        anal_score = len(tokens & _ANALYTICAL_SIGNALS)
        crea_score = len(tokens & _CREATIVE_SIGNALS)
        domain_scores = {
            Domain.TECHNICAL: tech_score,
            Domain.ANALYTICAL: anal_score,
            Domain.CREATIVE: crea_score,
            Domain.OPERATIONAL: 0,
        }
        domain = max(domain_scores, key=lambda d: domain_scores[d])
        if domain_scores[domain] == 0:
            domain = Domain.OPERATIONAL

        confidence = 0.7
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            confidence = 0.95
        elif requires_web:
            confidence = 0.9
        elif complexity == Complexity.SIMPLE and not requires_code:
            confidence = 0.85
        elif expert_hits >= 2:
            confidence = 0.88
        if len(goal.split()) <= 4:
            confidence = min(confidence, 0.65)

        return GoalProperties(
            raw_goal=goal,
            complexity=complexity,
            domain=domain,
            risk=risk,
            time_sensitivity=time_sensitivity,
            kb_state=KnowledgeState.UNKNOWN,
            reversibility=reversibility,
            multi_step=estimated_steps > 1,
            is_generative=bool(
                tokens & {"write", "generate", "create", "draft", "compose"}
            ),
            requires_web=requires_web,
            requires_code=requires_code,
            requires_vision=bool(
                tokens & {"image", "photo", "screenshot", "vision", "ocr"}
            ),
            estimated_steps=estimated_steps,
            classifier_confidence=confidence,
        )

    async def classify_with_llm(
        self,
        goal: str,
        provider: Any,
        fast_props: GoalProperties | None = None,
    ) -> GoalProperties:
        base = fast_props or self.classify_fast(goal)
        if provider is None:
            return base
        if base.classifier_confidence > 0.85 or base.complexity != Complexity.MEDIUM:
            return base
        try:
            from app.providers.base import CompletionRequest, Message
            import json

            prompt = (
                "Classify this goal. Return ONLY JSON:\n"
                '{"complexity":"simple|medium|complex|expert",'
                '"domain":"technical|creative|analytical|operational|conversational",'
                '"risk":"low|medium|high|critical","requires_web":true|false,'
                '"requires_code":true|false,"estimated_steps":1-10,"confidence":0.0-1.0}'
                "\n\nGoal: " + goal[:500]
            )
            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=150,
                    temperature=0.0,
                )
            )
            data = json.loads(resp.content.strip())
            return GoalProperties(
                raw_goal=goal,
                complexity=Complexity(data.get("complexity", base.complexity.value)),
                domain=Domain(data.get("domain", base.domain.value)),
                risk=RiskLevel(data.get("risk", base.risk.value)),
                time_sensitivity=base.time_sensitivity,
                kb_state=base.kb_state,
                reversibility=base.reversibility,
                multi_step=int(data.get("estimated_steps", base.estimated_steps)) > 1,
                is_generative=base.is_generative,
                requires_web=bool(data.get("requires_web", base.requires_web)),
                requires_code=bool(data.get("requires_code", base.requires_code)),
                requires_vision=base.requires_vision,
                estimated_steps=int(data.get("estimated_steps", base.estimated_steps)),
                classifier_confidence=float(data.get("confidence", 0.8)),
            )
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("llm_classification_failed", error=str(exc))
            return base
