"""
Skill Selector
==============
Selects the top 1-3 most relevant skills for a given goal by embedding
trigger_hints and matching against the goal text.

Token-reduction strategy: skills narrow the tool selection (allowed_tools)
AND provide pre-compressed instructions, so loading a skill typically reduces
total context tokens vs. monolithic system prompts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


# Platform default skills (seeded in DB — this is the in-memory registry for
# when DB is unavailable or for initial seeding)
PLATFORM_SKILLS: list[dict[str, Any]] = [
    {
        "id": "skill-summarize-compress",
        "name": "summarize-and-compress",
        "trigger_hints": ["summarize", "compress", "brief", "tldr", "key points", "overview"],
        "instructions": (
            "Produce concise, citation-backed output. Never quote tool output verbatim — "
            "cite it instead (e.g. 'Source: [JIRA-123]'). Limit output to the fewest tokens "
            "that fully answer the goal."
        ),
        "allowed_tools": [],
        "token_estimate": 80,
    },
    {
        "id": "skill-web-research",
        "name": "web-research",
        "trigger_hints": [
            "search web",
            "find online",
            "research",
            "look up",
            "find information about",
        ],
        "instructions": (
            "Search multiple sources before answering. Cross-reference facts. "
            "Cite all sources with URLs. Flag conflicting information."
        ),
        "allowed_tools": ["web_search", "fetch_url"],
        "token_estimate": 90,
    },
    {
        "id": "skill-structured-reporting",
        "name": "structured-reporting",
        "trigger_hints": [
            "report",
            "generate report",
            "create report",
            "weekly report",
            "status report",
            "dashboard",
        ],
        "instructions": (
            "Produce a structured report with: 1) Executive Summary, 2) Key Metrics, "
            "3) Findings, 4) Recommendations. Use tables for comparative data."
        ),
        "allowed_tools": [],
        "token_estimate": 120,
    },
    {
        "id": "skill-code-review",
        "name": "code-review",
        "trigger_hints": [
            "review code",
            "code review",
            "PR review",
            "check pull request",
            "review PR",
        ],
        "instructions": (
            "Review for: correctness, security (injection, auth, secrets), performance, "
            "test coverage. Use severity levels: CRITICAL / HIGH / MEDIUM / LOW. "
            "Provide specific line references."
        ),
        "allowed_tools": ["github_get_pr", "github_list_files", "github_get_file"],
        "token_estimate": 150,
    },
    {
        "id": "skill-data-extraction",
        "name": "data-extraction",
        "trigger_hints": [
            "extract data",
            "parse",
            "extract fields",
            "get data from",
            "pull data",
        ],
        "instructions": (
            "Extract structured data as JSON. Include confidence scores for ambiguous extractions. "
            "Flag missing or unclear fields explicitly."
        ),
        "allowed_tools": [],
        "token_estimate": 70,
    },
    {
        "id": "skill-frontend-design",
        "name": "frontend-design",
        "trigger_hints": [
            "build UI",
            "create page",
            "design component",
            "build frontend",
            "create website",
        ],
        "instructions": (
            "Use semantic design tokens (not raw colors). Ensure WCAG 2.2 AA accessibility. "
            "Include loading, empty, and error states. Use TypeScript strict mode."
        ),
        "allowed_tools": [],
        "token_estimate": 100,
    },
    {
        "id": "skill-knowledge-graphify",
        "name": "knowledge-graphify",
        "trigger_hints": [
            "extract entities",
            "knowledge graph",
            "entity relation",
            "map relationships",
            "ontology",
        ],
        "instructions": (
            "Extract entities and their relationships from content. "
            "Identify: people, organizations, concepts, events, and how they relate. "
            "Output as structured JSON: {entities: [...], relationships: [...]}. "
            "Never invent relationships not present in the source."
        ),
        "allowed_tools": ["document_reader", "web_search"],
        "token_estimate": 110,
    },
    {
        "id": "skill-headroom",
        "name": "headroom",
        "trigger_hints": [
            "compress",
            "concise",
            "brief output",
            "token efficient",
            "summarize response",
        ],
        "instructions": (
            "Produce maximally concise output. Never restate what the tool already returned"
            " — cite it. "
            "Skip ceremony: no 'I found that...', no restating the question. "
            "Lead with the answer. Use bullet points over prose for lists."
        ),
        "allowed_tools": [],
        "token_estimate": 50,
    },
]


@dataclass
class SelectedSkill:
    skill_id: str
    name: str
    instructions: str
    allowed_tools: list[str]
    match_score: float
    trigger_matched: str


class SkillSelector:
    """
    Selects relevant skills for a goal using keyword matching on trigger_hints.
    Falls back to platform skills when DB is unavailable.
    """

    def __init__(self, db_skills: list[dict[str, Any]] | None = None) -> None:
        self._skills = db_skills if db_skills is not None else PLATFORM_SKILLS

    def select(
        self,
        goal: str,
        *,
        max_skills: int = 3,
        max_tokens: int = 500,
    ) -> list[SelectedSkill]:
        """Return up to max_skills relevant skills for the goal."""
        goal_lower = goal.lower()
        scored: list[tuple[float, dict[str, Any], str]] = []

        for skill in self._skills:
            if not skill.get("is_active", True):
                continue

            best_score = 0.0
            best_trigger = ""

            for hint in skill.get("trigger_hints") or []:
                hint_lower = hint.lower()
                # Direct substring match
                if hint_lower in goal_lower:
                    score = len(hint_lower) / max(len(goal_lower), 1)
                    if score > best_score:
                        best_score = score
                        best_trigger = hint

            if best_score > 0:
                scored.append((best_score, skill, best_trigger))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        selected: list[SelectedSkill] = []
        total_tokens = 0

        for score, skill, trigger in scored[:max_skills]:
            tok = skill.get("token_estimate", 100)
            if total_tokens + tok > max_tokens:
                continue

            selected.append(
                SelectedSkill(
                    skill_id=skill.get("id", ""),
                    name=skill.get("name", ""),
                    instructions=skill.get("instructions", ""),
                    allowed_tools=list(skill.get("allowed_tools") or []),
                    match_score=score,
                    trigger_matched=trigger,
                )
            )
            total_tokens += tok

        if selected:
            logger.info(
                "skills_selected",
                count=len(selected),
                skills=[s.name for s in selected],
                total_tokens=total_tokens,
            )

        return selected

    def build_skills_context(self, selected: list[SelectedSkill]) -> str:
        """Build a compact skill context block to inject into the planner prompt."""
        if not selected:
            return ""

        parts = ["[Active Skills]"]
        for s in selected:
            parts.append(f"• {s.name}: {s.instructions}")

        return "\n".join(parts)
