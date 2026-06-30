"""Harvey AI MCP server — AI-powered legal research, drafting, and document analysis.

Environment:
  HARVEY_API_KEY: Harvey AI API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.harvey.ai/v1"

TOOL_DEFINITIONS = [
    {
        "name": "harvey_legal_research",
        "description": "Conduct AI-powered legal research on a specific legal question or topic",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Legal research question or topic to investigate"},
                "jurisdiction": {"type": "string", "description": "Jurisdiction (e.g. US Federal, New York, UK)"},
                "practice_area": {"type": "string", "description": "Practice area (e.g. contracts, IP, employment)"},
                "include_cases": {"type": "boolean", "description": "Include case law citations"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "harvey_draft_contract",
        "description": "Draft a contract or legal agreement based on provided parameters",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_type": {"type": "string", "description": "Type of contract (e.g. NDA, employment, SaaS)"},
                "parties": {"type": "array", "description": "List of party names and roles", "items": {"type": "object"}},
                "key_terms": {"type": "object", "description": "Key terms and conditions to include"},
                "jurisdiction": {"type": "string", "description": "Governing law jurisdiction"},
            },
            "required": ["contract_type"],
        },
    },
    {
        "name": "harvey_analyze_document",
        "description": "Analyze a legal document to extract key clauses, risks, and obligations",
        "parameters": {
            "type": "object",
            "properties": {
                "document_text": {"type": "string", "description": "Full text of the legal document to analyze"},
                "analysis_type": {"type": "string", "description": "Analysis type: risk, summary, comparison, redline"},
                "focus_areas": {"type": "array", "description": "Specific areas to focus on", "items": {"type": "string"}},
            },
            "required": ["document_text"],
        },
    },
    {
        "name": "harvey_find_precedents",
        "description": "Find relevant legal precedents and case law for a given legal issue",
        "parameters": {
            "type": "object",
            "properties": {
                "legal_issue": {"type": "string", "description": "Legal issue or question to find precedents for"},
                "jurisdiction": {"type": "string", "description": "Jurisdiction to search in"},
                "date_range": {"type": "string", "description": "Date range for cases (e.g. 2010-2024)"},
                "limit": {"type": "integer", "description": "Maximum precedents to return"},
            },
            "required": ["legal_issue"],
        },
    },
    {
        "name": "harvey_summarize_case",
        "description": "Generate a concise summary of a legal case or decision",
        "parameters": {
            "type": "object",
            "properties": {
                "case_text": {"type": "string", "description": "Full text or citation of the case"},
                "summary_length": {"type": "string", "description": "Summary length: brief, standard, detailed"},
                "include_holdings": {"type": "boolean", "description": "Include key holdings"},
            },
            "required": ["case_text"],
        },
    },
    {
        "name": "harvey_check_compliance",
        "description": "Check a document or business practice for compliance with specified regulations",
        "parameters": {
            "type": "object",
            "properties": {
                "document_text": {"type": "string", "description": "Text to check for compliance"},
                "regulations": {"type": "array", "description": "List of regulations/frameworks to check against", "items": {"type": "string"}},
                "jurisdiction": {"type": "string", "description": "Jurisdiction for compliance check"},
            },
            "required": ["document_text", "regulations"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("HARVEY_API_KEY", "")
    if not api_key:
        return {"error": "HARVEY_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            if tool_name == "harvey_legal_research":
                r = await client.post(
                    f"{BASE_URL}/research",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "harvey_draft_contract":
                r = await client.post(
                    f"{BASE_URL}/draft",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "harvey_analyze_document":
                r = await client.post(
                    f"{BASE_URL}/analyze",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "harvey_find_precedents":
                r = await client.post(
                    f"{BASE_URL}/precedents",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "harvey_summarize_case":
                r = await client.post(
                    f"{BASE_URL}/summarize",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "harvey_check_compliance":
                r = await client.post(
                    f"{BASE_URL}/compliance",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
