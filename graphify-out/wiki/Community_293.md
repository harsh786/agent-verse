# Community 293

> 23 nodes · cohesion 0.12

## Key Concepts

- **http_step.py** (13 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`
- **HTTPStepNode** (8 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`
- **security.py** (7 connections) — `agent-verse-backend/app/workflow/security.py`
- **SSRFBlockedError** (6 connections) — `agent-verse-backend/app/workflow/security.py`
- **SecretMasker** (5 connections) — `agent-verse-backend/app/workflow/security.py`
- **SSRFGuard** (5 connections) — `agent-verse-backend/app/workflow/security.py`
- **.execute()** (5 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`
- **.mask()** (4 connections) — `agent-verse-backend/app/workflow/security.py`
- **.__init__()** (4 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`
- **._deep_mask()** (3 connections) — `agent-verse-backend/app/workflow/security.py`
- **.validate()** (3 connections) — `agent-verse-backend/app/workflow/security.py`
- **._parse_timeout()** (3 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`
- **Any** (2 connections)
- **Any** (2 connections)
- **PermissionError** (1 connections)
- **Security utilities for workflow execution. SSRFGuard: Blocks HTTP steps from…** (1 connections) — `agent-verse-backend/app/workflow/security.py`
- **Redacts vault-resolved secret values before DB persistence. The ContextResolver…** (1 connections) — `agent-verse-backend/app/workflow/security.py`
- **Return a copy of resolved_input with vault values redacted.** (1 connections) — `agent-verse-backend/app/workflow/security.py`
- **Raised when an HTTP step URL resolves to a blocked address.** (1 connections) — `agent-verse-backend/app/workflow/security.py`
- **Validates HTTP step URLs against SSRF blocklist.** (1 connections) — `agent-verse-backend/app/workflow/security.py`
- **Raises SSRFBlockedError if URL is unsafe.** (1 connections) — `agent-verse-backend/app/workflow/security.py`
- **HTTPStepNode — authenticated HTTP call with SSRF guard + circuit breaker.** (1 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`
- **Parse '30s', '5m', '2h' → float seconds.** (1 connections) — `agent-verse-backend/app/workflow/steps/http_step.py`

## Relationships

- [Community 89](Community_89.md) (5 shared connections)
- [Community 153](Community_153.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Community 110](Community_110.md) (2 shared connections)
- [Community 90](Community_90.md) (2 shared connections)
- [Community 147](Community_147.md) (1 shared connections)
- [Community 155](Community_155.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/workflow/security.py`
- `agent-verse-backend/app/workflow/steps/http_step.py`

## Audit Trail

- EXTRACTED: 44 (90%)
- INFERRED: 5 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*