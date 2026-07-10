# 09 — 100 Real-World Use Cases

> **100 production-grade use cases** fully grounded in AgentVerse's actual implementation.
> Every use case cites real `app/` files, real MCP server modules, real strategy keys,
> and real model names from the codebase.

## Organisation

The 100 use cases are split into 10 files of 10 each (plus one file of 20 for complex workflows) in the [`use-cases/`](./use-cases/) folder:

| File | Use Cases | Domains |
|------|-----------|---------|
| [use-cases-01-10.md](./use-cases/use-cases-01-10.md) | 1–10 | Software Engineering, DevOps/SRE |
| [use-cases-11-20.md](./use-cases/use-cases-11-20.md) | 11–20 | Security Operations, Compliance |
| [use-cases-21-30.md](./use-cases/use-cases-21-30.md) | 21–30 | Compliance/Audit, Legal/Policy, Customer Support |
| [use-cases-31-40.md](./use-cases/use-cases-31-40.md) | 31–40 | Sales/CRM, Finance Operations |
| [use-cases-41-50.md](./use-cases/use-cases-41-50.md) | 41–50 | HR Operations, Product Management, Project Management |
| [use-cases-51-60.md](./use-cases/use-cases-51-60.md) | 51–60 | Project Management, Healthcare, Insurance, Banking |
| [use-cases-61-70.md](./use-cases/use-cases-61-70.md) | 61–70 | Banking, E-commerce, Education, Research |
| [use-cases-71-80.md](./use-cases/use-cases-71-80.md) | 71–80 | Research, Data Analytics, Executive Reporting, RPA, Multimodal |
| [use-cases-81-100.md](./use-cases/use-cases-81-100.md) | 81–100 | Multimodal, Incident Response, Procurement, Marketing, Field Ops, Multi-domain Complex |

## Format

Every use case answers:

| Field | What it covers |
|-------|----------------|
| **Goal** | Natural-language goal submitted to AgentVerse |
| **Business problem** | Why this matters in production |
| **Actors** | User, agent, external systems |
| **Inputs** | Documents, APIs, events, data |
| **Agent pattern** | Pattern selected by PatternAssembler with reason |
| **RAG pattern** | Strategy from `app/rag/engine.py:retrieve()` dispatch |
| **Memory used** | Which of the 10 memory types activate |
| **Ingestion path** | Source → parser → chunker → embedder → index |
| **Retrieval path** | Query → strategy → reranking → citations |
| **Model routing** | Planner/executor/verifier model choices |
| **Guardrails and governance** | GuardrailChecker, PolicyEngine, HITL conditions |
| **End-to-end flow** | 5–8 numbered steps through AgentGraph |
| **Observability** | SSE events, Prometheus metrics, cost |
| **Eval path** | RuntimeScorecard dimensions scored |
| **Expected output** | Concrete artifact produced |
| **Failure modes** | Specific failures and recovery paths |
| **Code references** | Real `app/` files involved |

## Domain Coverage

| Domain | Use Cases |
|--------|-----------|
| Software Engineering | 1–8 |
| DevOps / SRE | 9–14 |
| Security Operations | 15–19 |
| Compliance / Audit | 20–24 |
| Legal / Policy Review | 25–28 |
| Customer Support | 29–32 |
| Sales / CRM | 33–36 |
| Finance Operations | 37–40 |
| HR Operations | 41–44 |
| Product Management | 45–48 |
| Project Management | 49–52 |
| Healthcare Administration | 53–56 |
| Insurance Operations | 57–59 |
| Banking / Fintech | 60–62 |
| E-commerce | 63–65 |
| Education / Training | 66–68 |
| Research / Knowledge Mgmt | 69–71 |
| Data Analytics | 72–74 |
| Executive Reporting | 75–76 |
| Browser / RPA Automation | 77–79 |
| Multimodal Document Intelligence | 80–82 |
| Incident Response | 83–85 |
| Procurement / Vendor Mgmt | 86–88 |
| Marketing Operations | 89–91 |
| Field Operations | 92–94 |
| Multi-domain Complex Workflows | 95–100 |
