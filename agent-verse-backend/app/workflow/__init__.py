"""Workflow Automation Engine — predefined multi-step workflow execution.

This package provides the complete workflow engine:
  - DSL: YAML/JSON workflow definition parsing and validation
  - Registry: plug-in step type system
  - Compiler: DSL → LangGraph StateGraph
  - Runner: Celery-backed async execution with HITL, SSE, OTEL
  - HITL: 20-feature human-in-the-loop gateway extension
  - Templates: 25 system workflow templates

The engine sits alongside the Goals Engine (dynamic LLM planning)
and reuses all existing infrastructure: LLMProvider, MCPClient,
KnowledgeStore, HITLGateway, Redis checkpointer, Celery queues, AuditLog.

Step types are registered at import time via StepTypeRegistry.
"""
from __future__ import annotations

# Registry import triggers built-in step type registration
from app.workflow.registry import StepTypeRegistry

__all__ = ["StepTypeRegistry"]
