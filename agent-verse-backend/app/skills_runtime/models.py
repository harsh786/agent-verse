"""Skills Runtime data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SkillScope(StrEnum):
    PLATFORM = "platform"  # Available to all tenants
    TENANT = "tenant"  # Tenant-specific
    AGENT = "agent"  # Agent-specific


class SkillStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    BETA = "beta"


@dataclass
class SkillDefinition:
    """A skill definition."""

    skill_id: str
    name: str
    description: str
    scope: SkillScope
    trigger_hints: list[str] = field(default_factory=list)
    instructions: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    permissions_required: list[str] = field(default_factory=list)
    output_contract: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0.0"
    status: SkillStatus = SkillStatus.ACTIVE
    tenant_id: str | None = None
    author: str = "system"
    is_builtin: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass
class SkillExecution:
    """A skill execution trace."""

    execution_id: str
    skill_id: str
    tenant_id: str
    goal_id: str | None = None
    input_context: str = ""
    output: str = ""
    success: bool = False
    error: str | None = None
    duration_ms: float = 0.0
    model_used: str | None = None
    created_at: str | None = None


# Built-in platform skills
BUILTIN_SKILLS = [
    {
        "skill_id": "graphify",
        "name": "Graphify",
        "description": "Convert any input (code, docs, papers) into a knowledge graph",
        "trigger_hints": [
            "create knowledge graph",
            "graphify this",
            "extract entities from",
            "build graph from",
        ],
        "instructions": (
            "Extract entities and relationships from the provided content"
            " and create a structured knowledge graph."
        ),
        "allowed_tools": ["knowledge_graph_extract", "knowledge_store_search"],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "headroom",
        "name": "Headroom",
        "description": "Compress large outputs to reduce context and token usage",
        "trigger_hints": [
            "compress this",
            "reduce tokens",
            "summarize for context",
            "trim output",
        ],
        "instructions": (
            "Analyze the provided content and produce a compressed version that preserves"
            " all key information while reducing token count."
        ),
        "allowed_tools": [],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "code_review",
        "name": "Code Review",
        "description": "Review code for bugs, security issues, and best practices",
        "trigger_hints": [
            "review this code",
            "code review",
            "check for bugs",
            "security audit code",
        ],
        "instructions": (
            "Perform a thorough code review checking for: bugs, security vulnerabilities,"
            " performance issues, code style, and best practices."
        ),
        "allowed_tools": ["read_file", "search_code"],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "rag_eval",
        "name": "RAG Evaluator",
        "description": "Evaluate RAG pipeline quality — precision, recall, and faithfulness",
        "trigger_hints": [
            "evaluate rag",
            "check rag quality",
            "rag precision",
            "retrieval quality",
        ],
        "instructions": (
            "Evaluate the RAG pipeline by checking: retrieval precision, recall,"
            " answer faithfulness, and citation quality."
        ),
        "allowed_tools": ["knowledge_store_search", "evaluate"],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "prompt_optimizer",
        "name": "Prompt Optimizer",
        "description": "Analyze and improve prompts for better LLM performance",
        "trigger_hints": [
            "optimize prompt",
            "improve this prompt",
            "better prompt for",
            "prompt engineering",
        ],
        "instructions": (
            "Analyze the prompt and suggest improvements for clarity, specificity,"
            " and expected output quality."
        ),
        "allowed_tools": [],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "security_review",
        "name": "Security Review",
        "description": "Security audit for code, config, and infrastructure",
        "trigger_hints": [
            "security review",
            "vulnerability scan",
            "security audit",
            "check for vulnerabilities",
        ],
        "instructions": (
            "Perform a security review checking for: injection vulnerabilities,"
            " authentication issues, authorization gaps, data exposure,"
            " and insecure configurations."
        ),
        "allowed_tools": ["read_file", "search_code", "knowledge_store_search"],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "test_writer",
        "name": "Test Writer",
        "description": "Generate comprehensive tests for code",
        "trigger_hints": [
            "write tests",
            "generate unit tests",
            "create test suite",
            "add tests for",
        ],
        "instructions": (
            "Generate comprehensive tests including unit tests, integration tests,"
            " edge cases, and property-based tests."
        ),
        "allowed_tools": ["read_file", "write_file", "search_code"],
        "is_builtin": True,
        "version": "1.0.0",
    },
    {
        "skill_id": "qa_engineer",
        "name": "QA Engineer",
        "description": "Generate QA test plans and identify test scenarios",
        "trigger_hints": [
            "qa test plan",
            "testing strategy",
            "test scenarios",
            "quality assurance",
        ],
        "instructions": (
            "Create a comprehensive QA strategy including test cases, acceptance criteria,"
            " regression tests, and edge case identification."
        ),
        "allowed_tools": [],
        "is_builtin": True,
        "version": "1.0.0",
    },
]
